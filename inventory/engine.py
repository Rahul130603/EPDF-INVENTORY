from dataclasses import dataclass, asdict, replace
from pathlib import Path
import hashlib
import json
import re
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from queue import Empty
import pymupdf
import numpy as np
from .discovery import discover, IMAGES, Book, structured_pdf, book_part, spreadsheet_pdf
from .metadata import find_isbns, language, language_evidence, title_from_text
from .vision import DEFAULT_MODELS, check_models, ocr_page, picture_regions, analyse_image

DEFAULT_WORKERS = 4
MAX_WORKERS = 6


@dataclass
class Options:
    grouping: str = "auto"
    image_mode: str = "pictures"  # pictures = estimated regions, files = external files only
    ocr: bool = True
    models: str = str(DEFAULT_MODELS)
    online_titles: bool = True
    workers: int = DEFAULT_WORKERS
    isbn_filter: tuple | None = None
    fast_isbn_folders: bool = False


_worker_stop = None
_worker_progress = None


def initialize_worker(stop, messages):
    global _worker_stop, _worker_progress
    import os
    import cv2
    os.environ['OMP_THREAD_LIMIT'] = '1'
    cv2.setNumThreads(1)
    _worker_stop, _worker_progress = stop, messages


def process_book(index, book, options):
    # PDF documents never cross processes; each worker opens its own files.
    return analyse_book(book,options,_worker_stop.is_set,
                        lambda message:_worker_progress.put((index,message)))


def read_text(path: Path) -> str:
    with path.open("rb") as handle:
        data = handle.read(2_000_000)
    for encoding in ("utf-8-sig", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    return data.decode("utf-8", errors="replace")


def pdf_title(page) -> str:
    candidates = []
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = " ".join(s["text"].strip() for s in spans).strip()
            if (4 <= len(text) <= 180 and not re.search(r"isbn|copyright|www\.|©", text, re.I)
                    and len(re.findall(r"[^\W\d_]", text)) >= 4):
                candidates.append((max(s["size"] for s in spans), text))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return ""


def analyse_pdf(path: Path, options: Options, cancel, progress, purpose="general") -> dict:
    result = {"text": "", "title": "", "pages": 0, "type": "NEEDS REVIEW", "images": 0,
              "notes": [], "regions": [], "metadata": ""}
    texts, kinds = [], []
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("Password-protected PDF")
        result["pages"] = len(doc)
        result["metadata"] = " ".join(str(v or "") for v in (doc.metadata or {}).values())
        sample = set(range(min(len(doc), 10)))
        unavailable = False
        for index, page in enumerate(doc):
            if purpose == "metadata" and index >= 10:
                break
            if cancel():
                raise InterruptedError("Cancelled")
            progress(f"{path.name} · {'metadata ' if purpose == 'metadata' else ''}page {index + 1}/{min(10,len(doc)) if purpose == 'metadata' else len(doc)}")
            text = page.get_text(sort=True)
            images = page.get_image_info()
            area = max(1, page.rect.get_area())
            full_scan = any(((pymupdf.Rect(i["bbox"]) * page.rotation_matrix) & page.rect).get_area() / area >= .65 for i in images)
            has_text = len(re.findall(r"[^\W\d_]", text)) >= 5
            if full_scan:
                kinds.append("SCANNED")
            elif has_text:
                kinds.append("EDITABLE")
            elif images or page.get_drawings():
                kinds.append("UNKNOWN")
            # Blank pages don't turn a text PDF into a mixed PDF.
            if index < 10 and not result["title"] and not full_scan:
                result["title"] = pdf_title(page)
            if (full_scan or (not has_text and index in sample)) and (index < 10 or purpose == "images"):
                if options.ocr:
                    try:
                        ocr_text, words = ocr_page(page, Path(options.models))
                        if len(ocr_text.strip()) > len(text.strip()):
                            text = ocr_text
                        if full_scan and options.image_mode == "pictures" and purpose != "metadata":
                            if page.rotation:
                                words = [tuple(pymupdf.Rect(word[:4]) * page.rotation_matrix) + tuple(word[4:]) for word in words]
                            pix = page.get_pixmap(dpi=120, colorspace=pymupdf.csRGB, alpha=False)
                            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                            boxes = picture_regions(rgb, words, page.rect.width, page.rect.height)
                            result["images"] += len(boxes)
                            result["regions"].append({"file": str(path), "page": index+1, "regions": boxes,
                                                      "render_size": [pix.width, pix.height]})
                    except Exception as exc:
                        unavailable = True
                        result["notes"].append(f"OCR failed on page {index+1}: {exc}")
                elif full_scan:
                    unavailable = True
                    result["notes"].append("OCR disabled; scanned pages cannot be fully analysed")
            if full_scan and index >= 10 and purpose == "general":
                unavailable = True
                result["notes"].append("OCR limited to first 10 pages; complete picture counting requires ISBN_img.pdf")
            if not full_scan and purpose != "metadata":
                # Count rendered raster occurrences (not unused image resources or tiny icons).
                seen = set()
                for image in images:
                    bbox = tuple(round(v, 1) for v in image["bbox"])
                    rect = (pymupdf.Rect(bbox) * page.rotation_matrix) & page.rect
                    if bbox not in seen and rect.get_area() / area >= .002 and min(image["width"], image["height"]) >= 30:
                        result["images"] += 1
                        seen.add(bbox)
                if page.get_drawings():
                    result["notes"].append("Vector graphics present; raster count may omit diagrams")
            if index in sample:
                texts.append(text[:16000])
                if index < 10 and not result["title"]:
                    result["title"] = title_from_text(text)
            if index == 0:
                result['first_page_title'] = ((pdf_title(page) if not full_scan else '')
                                              or title_from_text(text))
        result["text"] = "\n".join(texts)
        result['language_pages'] = texts
        types = set(kinds)
        if {"SCANNED", "EDITABLE"}.issubset(types):
            result["type"] = "MIXED"
        elif "UNKNOWN" in types or not types:
            result["type"] = "NEEDS REVIEW"
        else:
            result["type"] = next(iter(types))
        if "SCANNED" in types:
            result["notes"].append("Full-page raster detected; scanned classification includes OCR text-layer PDFs")
            if options.image_mode == "pictures" and purpose != "metadata":
                result["notes"].append("Scanned-page picture count is a layout estimate; verify regions in audit JSON")
        if unavailable:
            result["images"] = None
        if purpose == "metadata":
            result["images"] = None
            result["notes"].append("Title, language and classification use first 10 pages only; total page count read directly from PDF")
    return result


def analyse_book(book: Book, options: Options, cancel, progress) -> dict:
    if any(structured_pdf(path) for path in book.files):
        return analyse_structured_book(book,options,cancel,progress)
    notes = list(book.notes)
    texts, pdfs, external, regions, metadata = [], [], [], [], []
    language_samples = []
    title = ""
    for path in book.files:
        if cancel():
            raise InterruptedError("Cancelled")
        suffix = path.suffix.lower()
        progress(path.name)
        try:
            if suffix == ".pdf":
                result = analyse_pdf(path, options, cancel, progress)
                pdfs.append(result)
                texts.append(result["text"])
                language_samples.extend(result.get('language_pages',[result['text']]))
                metadata.append(result["metadata"])
                title = title or result["title"]
                notes.extend(result["notes"])
                regions.extend(result["regions"])
            elif suffix == ".txt":
                text = read_text(path)
                texts.append(text)
                language_samples.append(text)
                title = title or title_from_text(text)
            else:
                external.append(path)
        except InterruptedError:
            raise
        except Exception as exc:
            notes.append(f"ERROR: {path.name}: {exc}")
    external_count, image_frames, external_failed = 0, 0, False
    for path in external:
        if cancel():
            raise InterruptedError("Cancelled")
        if options.ocr:
            try:
                text, count, frames, evidence = analyse_image(path, Path(options.models), cancel)
                texts.append(text)
                language_samples.append(text)
                title = title or title_from_text(text)
                external_count += count
                image_frames += frames
                regions.extend(evidence)
            except InterruptedError:
                raise
            except Exception as exc:
                external_failed = True
                notes.append(f"ERROR: {path.name}: {exc}")
        else:
            external_failed = True
            notes.append("OCR disabled; image content not analysed")
    body = "\n".join(texts)
    # Filename ISBNs have priority, followed by content, followed by PDF metadata.
    isbns = find_isbns("\n".join(p.name for p in book.files))
    for isbn in find_isbns(body + "\n" + "\n".join(metadata)):
        if isbn not in isbns:
            isbns.append(isbn)
    if not isbns:
        notes.append("ISBN not found")
    elif len(isbns) > 1:
        notes.append("Multiple ISBNs found (editions/formats); verify selected ISBN: " + ", ".join(isbns))
    lang, confidence = language(language_samples)
    if lang == "NEEDS REVIEW":
        notes.append("Insufficient or ambiguous book-language evidence")
    if not title:
        title = next((p.stem for p in book.files if p.suffix.lower() == ".pdf"), book.files[0].stem)
        notes.append("Title uses filename fallback")
    else:
        notes.append("Title extracted heuristically; verify against title page")
    if len(pdfs) > 1:
        notes.append("Multiple PDFs grouped; page/type/image counts use first PDF only")
    primary = pdfs[0] if pdfs else None
    page_count = primary["pages"] if primary else (image_frames if external and not external_failed else "N/A")
    book_type = primary["type"] if primary else ("SCANNED" if external else "EDITABLE")
    if not primary and any(p.suffix.lower() == ".pdf" for p in book.files):
        book_type, page_count = "NEEDS REVIEW", "N/A"
    if options.image_mode == "files":
        image_count = len(external)
        method = "External image FILE count (TIFF file = 1); PDF images excluded"
    elif external:
        # Avoid double-counting separate exports of pictures already embedded in PDF.
        image_count = "NEEDS REVIEW" if external_failed else external_count
        method = "External illustration regions estimated; PDF pictures excluded to avoid double counting"
        notes.append("External picture regions are estimates; verify count and source grouping")
    else:
        image_count = (primary["images"] if primary["images"] is not None else "NEEDS REVIEW") if primary else 0
        method = "PDF raster occurrences + estimated illustration regions on scanned pages"
        if not primary and any(p.suffix.lower() == ".pdf" for p in book.files):
            image_count = "NEEDS REVIEW"
    return {"isbn": isbns[0] if isbns else "NOT FOUND", "title": title, "language": lang,
            "book_type": book_type, "page_count": page_count, "image_count": image_count,
            "sources": [str(p) for p in book.files], "notes": sorted(set(notes)),
            "language_confidence": confidence, "image_method": method,
            "external_image_files": len(external), "regions": regions, "all_isbns": isbns}


def analyse_structured_book(book: Book, options: Options, cancel, progress) -> dict:
    """TEXT owns metadata; IMAGE and matching SPREADSHEET PDFs supply pictures."""
    notes = list(book.notes)
    sources = {role:[] for role in ('txt','img')}
    isbn = None
    for path in book.files:
        match = structured_pdf(path)
        if match:
            isbn = isbn or match[0]
            sources[match[1]].append(path)
    results = {}
    image_results = []
    image_sources = []
    image_counts_by_source = {}
    image_failed = False
    for role in ('txt','img'):
        paths = sorted(sources[role],key=lambda p:str(p).casefold())
        if not paths:
            if role == 'img' and any(spreadsheet_pdf(p) for p in book.files):
                notes.append('Missing ISBN_img.pdf; image count uses matching SPREADSHEET PDFs only')
            else:
                notes.append(f"Missing ISBN_{role}.pdf; {'metadata' if role == 'txt' else 'image count'} needs review")
            continue
        if len(paths) > 1:
            notes.append(f"Multiple ISBN_{role}.pdf sources; using {paths[0]}")
        if cancel():
            raise InterruptedError('Cancelled')
        try:
            # Structured files always count illustrations in _img, irrespective of
            # the legacy external-file-count setting used for unstructured folders.
            results[role] = analyse_pdf(paths[0],replace(options,image_mode='pictures'),cancel,progress,
                                        purpose='metadata' if role == 'txt' else 'images')
            notes.extend(results[role]['notes'])
            if role == 'img':
                image_results.append(results[role])
                image_sources.append(str(paths[0]))
                image_counts_by_source[str(paths[0])] = results[role]['images']
        except InterruptedError:
            raise
        except Exception as exc:
            notes.append(f"ERROR: {paths[0].name}: {exc}")
            if role == 'img':
                image_failed = True
    for path in sorted((p for p in book.files if spreadsheet_pdf(p)),key=lambda p:str(p).casefold()):
        if cancel():
            raise InterruptedError('Cancelled')
        image_sources.append(str(path))
        try:
            result = analyse_pdf(path,replace(options,image_mode='pictures'),cancel,progress,purpose='images')
            image_results.append(result)
            image_counts_by_source[str(path)] = result['images']
            notes.extend(result['notes'])
        except InterruptedError:
            raise
        except Exception as exc:
            image_failed = True
            notes.append(f'ERROR: {path.name}: {exc}')
    text_result = results.get('txt')
    body = text_result['text'] if text_result else ''
    lang_evidence = language_evidence(text_result.get('language_pages',[body]) if text_result else [])
    lang,confidence = lang_evidence['language'],lang_evidence['confidence']
    if lang == 'NEEDS REVIEW':
        notes.append(lang_evidence['reason'])
    title = text_result.get('first_page_title',text_result['title']) if text_result else ''
    if not title:
        title = 'NEEDS REVIEW'
        notes.append('Title not found on first TEXT page')
    else:
        notes.append('Local fallback title extracted from first TEXT page; verify against title page')
    candidates = [isbn] if isbn else []
    for value in find_isbns(body+'\n'+(text_result['metadata'] if text_result else '')):
        if value not in candidates:
            candidates.append(value)
    if len(candidates) > 1:
        notes.append('Additional ISBN candidates in TEXT: '+', '.join(candidates[1:]))
    image_count = (sum(result['images'] for result in image_results)
                   if image_results and not image_failed and
                   all(result['images'] is not None for result in image_results) and
                   not any(note.startswith('Cannot assign SPREADSHEET') for note in notes)
                   else 'NEEDS REVIEW')
    if any(spreadsheet_pdf(p) for p in book.files):
        notes.append('Matching IMAGE/SPREADSHEET PDFs included in picture count; their pages do not affect TEXT page count')
    if options.image_mode == 'files':
        notes.append('ISBN workflow counts pictures in IMAGE/SPREADSHEET PDFs; legacy external-file-count option does not apply')
    return {'isbn':isbn or 'NOT FOUND','title':title,'language':lang,
            'book_type':text_result['type'] if text_result else 'NEEDS REVIEW',
            'page_count':text_result['pages'] if text_result else 'N/A','image_count':image_count,
            'part':next((book_part(p) for p in book.files if structured_pdf(p)),None),
            'page_count_source':str(sorted(sources['txt'],key=lambda p:str(p).casefold())[0]) if text_result else None,
            'sources':[str(p) for p in book.files],'notes':sorted(set(notes)),
            'language_confidence':confidence,
            'language_evidence':lang_evidence,
            'image_method':'ISBN_img.pdf + matching IMAGE/SPREADSHEET PDFs: raster occurrences / scanned illustration-region estimates; TEXT and COVER excluded',
            'image_sources':image_sources,
            'image_counts_by_source':image_counts_by_source,
            'external_image_files':sum(p.suffix.lower() in IMAGES for p in book.files),
            'regions':[region for result in image_results for region in result['regions']],'all_isbns':candidates}


def run(root: Path, output: Path, options: Options, progress=lambda *args: None,
        stop: threading.Event | None = None, resume=True, on_book_complete=None) -> tuple[list[dict], bool]:
    from .export import export_workbook
    root, output = root.resolve(), output.resolve()
    if not root.is_dir():
        raise ValueError("Select an existing input folder")
    if type(options.workers) is not int or not 1 <= options.workers <= MAX_WORKERS:
        raise ValueError(f'Workers must be between 1 and {MAX_WORKERS}')
    if output.suffix.lower() != ".xlsx":
        raise ValueError("Output must have the .xlsx extension")
    if options.ocr:
        check_models(Path(options.models))
    stop = stop or threading.Event()
    missing_isbns=[]
    if options.isbn_filter is not None:
        from .isbn_filter import select_books
        # An ISBN-per-folder collection can contain tens of thousands of books
        # on a slow SMB share. Check exact folder paths before walking the root.
        # Fall back to the full search if any requested book is not a direct
        # child, preserving support for other folder layouts and filename ISBNs.
        requested = tuple(dict.fromkeys(options.isbn_filter))
        direct_folders = [root / isbn for isbn in requested]
        progress(0, 0, f'Checking {len(direct_folders)} ISBN folders directly…')
        if options.fast_isbn_folders or (direct_folders and all(folder.is_dir() for folder in direct_folders)):
            books = []
            for index, (isbn, folder) in enumerate(zip(requested, direct_folders), 1):
                if not folder.is_dir():
                    continue
                progress(0, 0, f'Discovering ISBN folder {index}/{len(direct_folders)}: {isbn}')
                books.extend(discover(folder, options.grouping))
        else:
            progress(0, 0, 'Searching all book folders; some ISBNs are not direct subfolders…')
            books = discover(root, options.grouping)
        books,missing_isbns=select_books(books,options.isbn_filter)
    else:
        books = discover(root, options.grouping)
    if not books and options.isbn_filter is None:
        raise ValueError("No supported PDF, TXT or image files found")
    output.parent.mkdir(parents=True, exist_ok=True)
    cache_path = output.with_suffix(".cache.json")
    audit_path = output.with_suffix(".audit.json")
    cache = {}
    if resume and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    results = []
    model_stats = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in Path(options.models).glob("*.traineddata")]
    cancelled = False
    scan_options = asdict(options)
    scan_options.pop('online_titles',None)
    scan_options.pop('workers',None)  # Parallelism must not invalidate unchanged OCR results.
    scan_options.pop('isbn_filter',None)
    scan_options.pop('fast_isbn_folders',None)  # Discovery scope does not change book analysis.
    def fingerprint_for(book):
        identity = {"version": 10, "options": scan_options, "models": model_stats,
                    "notes": book.notes,
                    "files": [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in book.files]}
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def complete(book, fingerprint, result, cached=False):
        # Only this coordinator writes caches/Excel and performs rate-limited title
        # lookups, so workers cannot overwrite each other's state or mix results.
        if not cached and not any(n.startswith(('ERROR:','OCR failed')) for n in result['notes']):
            cache[fingerprint] = result
            atomic_json(cache_path,cache)
        try:
            if options.online_titles:
                from .titles import enrich_title
                if stop.is_set():
                    raise InterruptedError('Cancelled')
                progress(len(results),len(books),'Looking up full title by ISBN: '+result['isbn'])
                result = enrich_title(result)
            if result.get('part') is not None:
                suffix = f" [Teil {result['part']}]"
                if not result['title'].endswith(suffix):
                    result = {**result, 'title': result['title'] + suffix}
            results.append(result)
            if on_book_complete is not None:
                on_book_complete(result)
            progress(len(results), len(books), "Completed " + book.files[0].name)
        except InterruptedError:
            return

    if options.workers == 1:
        for book in books:
            if stop.is_set():
                break
            fingerprint = fingerprint_for(book)
            progress(len(results),len(books),'Analysing '+book.files[0].name)
            try:
                cached = fingerprint in cache
                result = cache[fingerprint] if cached else analyse_book(book,options,stop.is_set,
                    lambda message:progress(len(results),len(books),message))
                complete(book,fingerprint,result,cached)
            except InterruptedError:
                break
    else:
        ctx = multiprocessing.get_context('spawn')
        shared_stop,messages = ctx.Event(),ctx.Queue()
        executor = None
        pending = {}
        iterator = iter(enumerate(books))
        exhausted = False
        try:
            while pending or not exhausted:
                if stop.is_set():
                    shared_stop.set()
                    exhausted = True
                # Keep only the selected number of books in flight, so cancel
                # does not leave a large queued backlog.
                while not exhausted and len(pending) < options.workers:
                    try:
                        index,book = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    fingerprint = fingerprint_for(book)
                    if fingerprint in cache:
                        complete(book,fingerprint,cache[fingerprint],True)
                        if stop.is_set():
                            exhausted = True
                            shared_stop.set()
                            break
                        continue
                    if executor is None:
                        executor = ProcessPoolExecutor(max_workers=options.workers,mp_context=ctx,
                            initializer=initialize_worker,initargs=(shared_stop,messages))
                    progress(len(results),len(books),'Processing in parallel: '+book.files[0].name)
                    future = executor.submit(process_book,index,book,options)
                    pending[future] = (book,fingerprint)
                if not pending:
                    continue
                finished,_ = wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                latest = None
                while True:
                    try:
                        _,latest = messages.get_nowait()
                    except Empty:
                        break
                if latest and not stop.is_set():
                    progress(len(results),len(books),f'{options.workers}-book parallel · '+latest)
                for future in finished:
                    book,fingerprint = pending.pop(future)
                    try:
                        complete(book,fingerprint,future.result())
                    except InterruptedError:
                        pass
        finally:
            shared_stop.set()
            if executor is not None:
                executor.shutdown(wait=True,cancel_futures=True)
            messages.close()
            messages.join_thread()
    cancelled = stop.is_set()
    for isbn in missing_isbns:
        row={'isbn':isbn,'title':'NOT FOUND','language':'N/A','book_type':'N/A','page_count':'N/A','image_count':'N/A',
             'sources':[],'notes':['No matching direct ISBN folder/file; other folders were not searched'
                                    if options.fast_isbn_folders else 'ISBN from uploaded Excel not found in folder/file names'],
             'language_confidence':0,'image_method':'Not processed: no matching book','external_image_files':0,'regions':[]}
        results.append(row)
        if on_book_complete:
            on_book_complete(row)
    atomic_json(audit_path, {"input": str(root), "options": asdict(options), "cancelled": cancelled,
                             "not_found_isbns":missing_isbns,
                             "completed": len(results), "total": len(books), "books": results})
    export_workbook(output, results, cancelled)
    return results, cancelled


def atomic_json(path: Path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

