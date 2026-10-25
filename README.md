# EPDF Inventory

Windows desktop app that scans local book folders and exports an Excel inventory.
Works offline after setup. Original book files are read only.

## Download on another Windows laptop

Download **BookScannerPDFExport.exe** from [the latest GitHub release](https://github.com/Rahul130603/EPDF-INVENTORY/releases/latest). Double-click it and open **http://localhost:4510**. Python is not required. Keep the app console open while scanning.

Alternatively, choose **Code > Download ZIP**, extract the ZIP, and double-click **StartPortable.bat**. It downloads the latest EXE into `dist/`, verifies its SHA-256 checksum, and launches it. Internet is required to download the EXE; scans work offline when **Online full titles** is disabled. The EXE is distributed in Releases rather than in the source ZIP because it exceeds GitHub's ordinary file-size limit. The launcher requires a public repository and Windows PowerShell 5.1 or newer; for a private repository, download the EXE through your signed-in GitHub browser instead.

On another laptop, start with **Books processed at once = 1**. Select that laptop's book folder and local Excel output path; upload its assigned ISBN sheet. Use Fast ISBN folder scan only for direct `Folder/ISBN/INPUT` layouts. All selected files must be accessible from that laptop. Never copy another machine's `.venv` folder.

PDF and image books now run in fresh isolated processes, including with one worker. A native worker crash is recorded as a NEEDS REVIEW row, with its sources, exit code and last page activity; other books continue. A worker with no page progress for five minutes is terminated and flagged. The coordinator saves an atomic `.partial.xlsx` after every completed row. Resume reuses valid checkpoints; checkpoint keys now use OCR model content hashes so temporary EXE extraction paths do not invalidate new checkpoints. Legacy source-runtime checkpoints are migrated where their existing fingerprints can be verified. `NOT FOUND` means the uploaded ISBN did not match a discovered book's folder/file names; check the audit JSON to identify missing ISBNs.

## Start

### Portable Windows EXE for employee laptops

The ready-to-copy browser dashboard is `dist/BookScannerPDFExport.exe`. On a Windows x64 laptop,
copy this one file and double-click it. The local dashboard opens in the default browser
at `http://localhost:4510`. Keep the EXE's console window open while scanning; close it
when finished. Python, Setup.bat, and a separate OCR-model download are not needed on
the employee laptop. The EXE contains the English/German OCR models and web assets.
The default report location is `Documents/EPDF Inventory/inventory.xlsx`; it can be
changed in the dashboard. No book files are uploaded. The executable unpacks to the
Windows temporary folder while it runs, so the laptop needs free temporary disk space.

For the 35-laptop workflow, give each employee their assigned ISBN Excel sheet. They
select the folder containing accessible book files, upload their sheet in **ISBN Excel
list**, and run only matching books. Use a different output path on each laptop. The
**Online full titles** checkbox can be turned off for offline or large batch runs.
Worker count 1–6 applies per laptop; it does not coordinate work between laptops.
Office shares can be pasted as UNC paths such as `\\192.168.0.198\output\books`.
Keep **Fast ISBN folder scan** enabled when books are directly under that folder as
`<ISBN>/INPUT/...`. With an uploaded ISBN list, the scanner opens only those ISBN
folders, so it does not enumerate thousands of unrelated server folders. Missing
direct folders become `NOT FOUND` review rows. Turn this setting off if books are
nested elsewhere or identified only by filenames. Save each report to a writable
local path unless the share is intended for report output.

To rebuild on the development PC after source changes:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\BuildExe.ps1
```

### Browser dashboard (port 4510)

To process only selected books, choose the main folder and upload an `.xlsx` file in **ISBN Excel list**. Put ISBNs in the first worksheet's `ISBN` column (a single-column list without a header also works). Duplicate ISBNs are removed and equivalent ISBN-10/13 values match. Only books whose filenames or book-folder names match the list are analysed; other books are not OCRed. Unmatched ISBNs are exported as `NOT FOUND` entries. Upload the list again after a page reload/server restart, or use **Clear list** to return to scanning all books. Maximum upload size: 5 MB.

Double-click **StartWeb.bat**, then open **http://localhost:4510**. The browser dashboard includes the pastel reference design, local folder picker, scan settings, progress, elapsed time, searchable results, review notes, report downloads and scan history. It reuses the same offline scanner. No files are uploaded: the server reads the folder you select directly from this computer.

The server listens only on `127.0.0.1:4510`. Keep the server running while using the dashboard. To launch manually:

```powershell
.\.venv\Scripts\python.exe web_app.py --port 4510
```

### Native desktop app

1. Install Python 3.11 or newer, including Tkinter (standard Windows installer).
2. Double-click **Setup.bat** once. Internet is needed to download Python packages and the official English/German OCR models.
3. Double-click **Start.bat**.
4. Browse to the folder containing your books, choose the Excel output and click **Scan & export Excel**.
5. Open the **Inventory** worksheet for the six requested columns. The **Review** worksheet lists source files, uncertainty, grouping notes and counting methods.

The main sheet has exactly these columns:

`ISBN, TITLE, LANGUAGE, EDITABLE OR SCANNED, PAGE COUNT, IMAGE COUNT`

## Folder layout and grouping

**ISBN collection workflow (preferred):** A valid `ISBN_txt.pdf` and matching `ISBN_img.pdf` are combined into one book across `INPUT/TEXT` and `INPUT/IMAGE` under the same book folder (for example, `9780820421834/`). `COVER` directories are pruned before traversal: their files are not listed, opened, OCRed or counted. This naming convention takes priority over the legacy grouping/count controls. When INPUT sources exist for a book, generated OUTPUT files (including POD copies and proofs) are excluded. ISBN cover PDFs such as ISBN_cvr_eu.pdf / ISBN_cvr_int.pdf and PDFs directly in COVER folders are excluded from standalone book discovery, preventing cover-only duplicate rows and 1–2-page counts.

- `ISBN_txt.pdf`: OCR/extract **only pages 1–10** for title, language and ISBN evidence. Editable/scanned classification also uses those sampled pages. Read total page count directly from the PDF without OCRing the rest.
- `ISBN_img.pdf`: Analyse all its pages for picture counting, adding matching IMAGE-folder SPREADSHEET PDFs. Text OCR on these image pages helps mask captions before detecting illustrations. Neither its metadata nor SPREADSHEET metadata overrides TEXT metadata.
- `COVER`: never entered, including nested subfolders and mixed-case folder names.
- `IMAGE/*SPREADSHEET*.pdf`: analyse every page for pictures and add their picture counts to the matching book/Teil. These files do not create book rows and never supply page counts, language or titles. A spreadsheet without a Teil number is attached only to a unique matching book; ambiguous multipart ownership is flagged for review rather than counted twice.
- Missing TEXT or IMAGE files are flagged for review; they are not silently replaced with cover files or another source. Picture counts remain estimates.

Example: a 232-page `9780820421834_txt.pdf` and 8-page `9780820421834_img.pdf` require at most **10 + 8 OCR pages**, producing one row with page count **232**. Existing checkpoints from the old workflow are automatically invalidated.

**Auto** groups PDF/TXT files in the same folder when their normalized filenames or valid filename ISBNs match. A PDF and TXT with the same stem produce one row. `Book_cover.jpg` can match `Book.pdf`.

Images without a filename match attach only when there is one book in the nearest parent folder. Multiple books plus unmatched images produce separate review entries. Files are never grouped across unrelated folders merely because they have the same name. Content ISBNs are extracted after grouping and do not silently merge books.

**One book per folder** groups all PDF/TXT files directly in a folder into one book. Images inside nested folders attach to the nearest book folder. An image-only folder becomes its own book. Use this option only when each folder really represents one book. If multiple PDFs are grouped, counts refer to the first successfully read PDF, ordered by filename; a review note identifies this.

Recommended layout:

```text
Books/
  Book One/
    Book One.pdf
    Book One.txt
    images/
      illustration_01.jpg
  Book Two/
    Book Two.pdf
```

## Extraction rules

**Online full titles:** By default, send the ISBN only to Google Books, with Open Library's ISBN edition API as fallback. Require the returned edition's ISBN-10/13 to match (equivalent ISBN-10/13 accepted), combine title + subtitle without duplication, and record the provider/URL in the audit plus provider in Review. No PDFs, images, folder paths or OCR text are uploaded. Successful lookups are cached locally for 30 days. If both services fail or have no matching record, retain the local PDF title and flag the fallback. Use CLI `--offline` to disable online title enrichment. Other extraction still runs locally, with the same 10-page TEXT limit and IMG-only picture count.

- **ISBN:** Validate ISBN-10/ISBN-13 checksums. Search filenames, first 10 pages and PDF metadata. First candidate wins, with all candidates recorded for review. Missing = `NOT FOUND`. Multiple print/e-book ISBNs require review.
- **Title:** For ISBN collection books, online lookup is preferred; if unavailable or disabled, use only the first TEXT PDF page (with OCR for scans). If that page has no usable title, flag `NEEDS REVIEW`. Generic PDFs use prominent text on early PDF pages, explicit `Title:`/`Titel:` text, or first plausible line; filename fallback is flagged. Titles remain heuristic and should be checked.
- **Language:** Compare the first 10 TEXT pages separately after excluding catalog, copyright and publisher-series pages. Short title pages contribute evidence; a single long page cannot swamp all others. Require at least 8 useful words overall, confidence ≥0.80 and a clear margin. All Lingua-supported languages can be returned by name (for example FRENCH, SPANISH and ITALIAN), without restricting output to English/German. Mixed/uncertain evidence still produces `NEEDS REVIEW`, with per-page evidence in the audit. OCR models remain English + German; other scripts need suitable OCR models before reliable recognition. No additional TEXT pages are OCRed.
- **Editable / scanned:** ISBN TEXT books use the first 10 pages; generic PDFs can inspect structure on remaining pages without OCR. A raster covering ≥65% of the page is treated as scanned, including scanned PDFs with OCR text layers. Text pages = `EDITABLE`, combined = `MIXED`. Blank pages are ignored. Unclear layouts = `NEEDS REVIEW`. Sampling can miss mixed content later in the book. “Editable” means text-based, not guaranteed Word-style editing.
- **Page count:** PDF page count. Image-only books use total image frames (including TIFF pages) if successfully processed. TXT alone = `N/A`.
- **Picture count (default):** If external images exist, OCR masks text and OpenCV estimates illustration regions inside them. Embedded PDF pictures are then excluded to avoid counting exported copies twice. With no external images, count visible raster occurrences on text PDF pages; scanned pages use region estimates. Counts include photos, drawings and logos, without semantic categorization. Vector-only diagrams are not counted and are flagged. Repeated pictures count on each occurrence. Multi-panel images may be merged/split; estimates always need review.
- **Image file count (optional):** Count external image files only, irrespective of their contents. A multipage TIFF counts as one file. PDF pictures are excluded in this mode.

Picture detection is a layout heuristic, not a trained semantic book-illustration model. It can miss faint drawings or miscount complex text/table layouts. `IMAGE COUNT` becomes `NEEDS REVIEW` when required OCR fails. The audit JSON records detected region coordinates and rendered dimensions for inspection.

Overlapping or nested picture-region boxes are consolidated before counting. When OCR incorrectly cuts one contiguous picture into separate regions, the detector reconnects fragments using the original page image. Disjoint pictures remain separate. Counts can still miss faint line drawings or mistake complex layouts; check the audit regions for uncertain books. Existing cache entries from older detector versions are recomputed automatically.

## Long runs, errors and resume

Completed books appear in the dashboard results table one by one while the next book is processing, including resumed/cached books. The completed-book count updates during the scan (browser polling refreshes about once per second). Full title lookup finishes before each row is published. Excel downloads become available after the final/partial workbook is saved. Completed rows remain visible if a later step fails.

By default, four books run concurrently in separate spawned Python processes. The browser and desktop dashboards let you select 1–6 books at once; the CLI accepts `--workers 1` through `--workers 6`. Start with 4 on this PC and try 6 after checking CPU and memory usage during a representative scan. Each worker opens its own PDFs and handles OCR independently; worker OCR/OpenCV thread limits prevent excessive CPU oversubscription. The coordinator alone writes checkpoints, performs rate-limited online lookups, publishes live rows and exports Excel. Entries appear in completion order, which can differ from folder order. Every completed book is checkpointed. Cancel stops scheduling new work and asks workers to stop between pages/frames. A worker still inside a native call after five seconds is terminated; completed rows remain in the partial report. Completed work is saved in a partial workbook. CLI `--workers 1` processes books sequentially while keeping PDFs/images isolated; worker count does not invalidate the existing OCR cache. The local API accepts integer `workers` values from 1 to 6 (default 4).

Running with the same output path resumes unchanged completed books. Changes in file path, size, modification timestamp, options or OCR models invalidate the cache. Failed books are retried. Delete the `.cache.json` beside the workbook or disable Resume for a fresh scan. Do not run two scans to the same output simultaneously. Close the workbook in Excel before replacing it.

Output files:

- `inventory.xlsx`: requested columns plus Review worksheet.
- `inventory.partial.xlsx`: completed entries saved after each book; available through Open Report while a scan runs. If this file is open in Excel, intermediate updates can be blocked; the scanner continues and writes the final workbook separately.
- `inventory.audit.json`: full source paths, all ISBN candidates, notes and picture region coordinates.
- `inventory.cache.json`: local resume checkpoint.

Corrupt/password-protected files are retained as review entries and do not stop other books. Disable OCR for a quick text-only run; scanned content then stays incomplete and is flagged. Processing scans takes substantially longer than processing text PDFs.

## Command line

```powershell
.\.venv\Scripts\python.exe app.py --folder "D:\Books" --output "D:\Reports\inventory.xlsx"
.\.venv\Scripts\python.exe app.py --folder "D:\Books" --workers 6
.\.venv\Scripts\python.exe app.py --folder "D:\Books" --grouping folder --image-mode files
.\.venv\Scripts\python.exe app.py --folder "D:\Books" --no-ocr --fresh
```

## Verification

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover ISBN validation, both languages, grouping, safe XLSX cells, scanned/editable/mixed PDFs, picture estimates, errors, cancellation, cache invalidation and 205-book batches. Real OCR tests require the downloaded models.

## Dependencies

PDF handling uses [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/page.html). Language detection uses [Lingua](https://github.com/pemistahl/lingua-py). OCR models come from the fixed 4.1.0 release of [Tesseract tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast), under Apache-2.0. Model source and downloaded SHA-256 hashes are stored in `models/sources.json`. XLSX writing uses Python's standard library. PyMuPDF is AGPL/commercial licensed; review its license before distributing a proprietary application.



### Teil / multipart collection books

`ISBN_txt_Teil_1.pdf` and `ISBN_txt_Teil_2.pdf` are separate inventory books. Match IMAGE files by the same Teil number; never combine page counts across parts. Titles include `[Teil N]` even after online lookup. Each row takes its total page count only from its own TEXT PDF. Missing TEXT yields `N/A`, never a cover or image page count. Collection cover images, SPREADSHEET PDFs and generated OUTPUT files do not create extra inventory rows. Old analysis checkpoints are invalidated for this change.

Picture counts in the structured workflow are the sum of estimates from the selected ISBN_img.pdf plus every matching IMAGE/SPREADSHEET PDF. With no ISBN_img.pdf, matching SPREADSHEET PDFs alone can supply pictures; any failed required source makes the total NEEDS REVIEW. Per-source counts are recorded in the audit JSON. Page count always comes from TEXT.
