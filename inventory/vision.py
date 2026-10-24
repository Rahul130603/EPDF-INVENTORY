"""Offline OCR and illustration-region estimates, never an exact semantic count."""
from pathlib import Path
import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps
from .paths import BUNDLE_ROOT

DEFAULT_MODELS = BUNDLE_ROOT / "models" / "tessdata"


def check_models(folder: Path) -> None:
    missing = [f"{lang}.traineddata" for lang in ("eng", "deu")
               if not (folder / f"{lang}.traineddata").is_file()]
    if missing:
        raise RuntimeError("English/German OCR models missing. Run Setup.bat first. Missing: " + ", ".join(missing))


def ocr_page(page, models: Path):
    tp = page.get_textpage_ocr(language="eng+deu", dpi=160, full=True, tessdata=str(models))
    return page.get_text("text", textpage=tp), page.get_text("words", textpage=tp)


def merge_picture_boxes(boxes: list[list[int]]) -> list[list[int]]:
    """Overlapping contour boxes describe fragments of one illustration.

    OCR can punch holes through halftone photographs, yielding separate contours
    whose bounding boxes overlap or contain one another. Count their union once.
    Keep disjoint illustrations separate, even when they are close together.
    """
    merged = []
    for source in boxes:
        box = list(source)
        index = 0
        while index < len(merged):
            other = merged[index]
            if min(box[2],other[2]) > max(box[0],other[0]) and min(box[3],other[3]) > max(box[1],other[1]):
                box = [min(box[0],other[0]),min(box[1],other[1]),max(box[2],other[2]),max(box[3],other[3])]
                merged.pop(index)
                index = 0  # The expanded union can now overlap an earlier box.
            else:
                index += 1
        merged.append(box)
    return sorted(merged,key=lambda b:(b[1],b[0]))


def contour_picture_boxes(mask: np.ndarray) -> list[list[int]]:
    h, w = mask.shape
    k = max(3, int(min(h, w) * .009))
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw * bh < w * h * .006 or bw < w * .04 or bh < h * .035:
            continue
        if max(bw / bh, bh / bw) > 15:
            continue
        if cv2.countNonZero(mask[y:y+bh, x:x+bw]) / (bw * bh) < .07:
            continue
        boxes.append([x, y, x + bw, y + bh])
    return merge_picture_boxes(boxes)


def join_ocr_split_pictures(boxes: list[list[int]], raw_boxes: list[list[int]], raw_mask: np.ndarray,
                            width: int, height: int) -> list[list[int]]:
    """Join fragments that belong to one contiguous pre-OCR illustration.

    OCR sometimes mistakes lettering or texture inside a photograph for words,
    cutting one image into several disjoint contours. The original ink contour
    can reconnect those pieces without joining pictures separated by white space.
    """
    used = set()
    joined = []
    for raw in raw_boxes:
        raw_area = (raw[2] - raw[0]) * (raw[3] - raw[1])
        if raw_area > width * height * .85:
            continue  # A page border is not one giant illustration.
        ink_density = cv2.countNonZero(raw_mask[raw[1]:raw[3], raw[0]:raw[2]]) / raw_area
        members = []
        for index, box in enumerate(boxes):
            if index in used:
                continue
            area = (box[2] - box[0]) * (box[3] - box[1])
            overlap = max(0, min(raw[2], box[2]) - max(raw[0], box[0])) * max(0, min(raw[3], box[3]) - max(raw[1], box[1]))
            if area and overlap / area >= .9:
                members.append(index)
        member_area = sum((boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]) for i in members)
        if len(members) >= 2 and (member_area >= raw_area * .25 or ink_density >= .65):
            used.update(members)
            joined.append(raw)
    return merge_picture_boxes([box for i, box in enumerate(boxes) if i not in used] + joined)


def remove_ocr_text_residue(boxes: list[list[int]], raw_boxes: list[list[int]],
                            raw_mask: np.ndarray, masked: np.ndarray) -> list[list[int]]:
    """Discard leftover word fragments in a paragraph mostly erased by OCR."""
    rejected = set()
    for raw in raw_boxes:
        x0, y0, x1, y1 = raw
        area = (x1 - x0) * (y1 - y0)
        raw_ink = cv2.countNonZero(raw_mask[y0:y1, x0:x1])
        if not raw_ink or raw_ink / area >= .35:
            continue
        remaining_ink = cv2.countNonZero(masked[y0:y1, x0:x1])
        if remaining_ink / raw_ink >= .12:
            continue
        for index, box in enumerate(boxes):
            box_area = (box[2] - box[0]) * (box[3] - box[1])
            overlap = max(0, min(raw[2], box[2]) - max(raw[0], box[0])) * max(0, min(raw[3], box[3]) - max(raw[1], box[1]))
            if box_area and overlap / box_area >= .9:
                rejected.add(index)
    return [box for index, box in enumerate(boxes) if index not in rejected]


def picture_regions(rgb: np.ndarray, words: list, page_width: float, page_height: float) -> list[list[int]]:
    """Remove OCR word boxes, join remaining ink, reject tiny/line-shaped regions.

    Counts photos, illustrations and diagrams as regions. Layout can split or merge
    pictures, and text OCR misses can cause false positives; every result is flagged.
    """
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY_INV)[1]
    margin = max(2, int(min(h, w) * .015))
    mask[:margin] = mask[-margin:] = 0
    mask[:, :margin] = mask[:, -margin:] = 0
    raw_mask = mask.copy()
    sx, sy = w / page_width, h / page_height
    for word in words:
        x0, y0, x1, y1 = word[:4]
        a, b = max(0, int(x0 * sx) - 3), max(0, int(y0 * sy) - 3)
        c, d = min(w, int(x1 * sx) + 3), min(h, int(y1 * sy) + 3)
        mask[b:d, a:c] = 0
    boxes = contour_picture_boxes(mask)
    if boxes and words:
        raw_boxes = contour_picture_boxes(raw_mask)
        boxes = remove_ocr_text_residue(boxes, raw_boxes, raw_mask, mask)
        if len(boxes) > 1:
            boxes = join_ocr_split_pictures(boxes, raw_boxes, raw_mask, w, h)
    return boxes


def analyse_image(path: Path, models: Path, cancel=lambda: False):
    """Support multi-frame TIFFs; PDF wrapper gives OCR boxes in page coordinates."""
    texts, count, frames, evidence = [], 0, 0, []
    with Image.open(path) as source:
        frame_count = getattr(source, "n_frames", 1)
        for index in range(frame_count):
            if cancel():
                raise InterruptedError("Cancelled")
            source.seek(index)
            image = ImageOps.exif_transpose(source.copy()).convert("RGB")
            image.thumbnail((1800, 2400))
            rgb = np.asarray(image)
            h, w = rgb.shape[:2]
            with pymupdf.open() as doc:
                page = doc.new_page(width=w, height=h)
                pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, rgb.tobytes(), False)
                page.insert_image(page.rect, pixmap=pix)
                text, words = ocr_page(page, models)
                boxes = picture_regions(rgb, words, w, h)
            texts.append(text)
            count += len(boxes)
            frames += 1
            evidence.append({"file": str(path), "frame": index + 1, "regions": boxes,
                             "render_size": [w, h]})
    return "\n".join(texts), count, frames, evidence
