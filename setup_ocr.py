"""Download the two official Tesseract fast models once; analysis stays offline."""
from pathlib import Path
from urllib.request import urlopen
import hashlib
import json

DEST = Path(__file__).resolve().parent / "models" / "tessdata"
# Fixed upstream release; models use Apache-2.0 (see upstream COPYING).
BASE = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for lang in ("eng", "deu"):
        target = DEST / f"{lang}.traineddata"
        if not target.exists():
            print(f"Downloading {lang} OCR model...", flush=True)
            with urlopen(f"{BASE}/{target.name}", timeout=120) as response:
                data = response.read()
            if len(data) < 100_000:
                raise RuntimeError("Invalid model download: " + lang)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
        hashes[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (DEST.parent / "sources.json").write_text(json.dumps({"source": BASE, "license": "Apache-2.0", "sha256": hashes}, indent=2), encoding="utf-8")
    print("English + German OCR ready. No separate Tesseract installation needed.")


if __name__ == "__main__":
    main()
