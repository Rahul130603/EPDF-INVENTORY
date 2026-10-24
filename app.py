"""Launch desktop app, or process a folder from the command line."""
import argparse
import multiprocessing
from pathlib import Path
import sys
from inventory.paths import REPORT_DIR


def main():
    # Redirected Windows terminals may use legacy encodings; filenames must not abort a scan.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Local English/German book inventory to Excel")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--output", type=Path, default=REPORT_DIR / "inventory.xlsx")
    parser.add_argument("--grouping", choices=["auto", "folder"], default="auto")
    parser.add_argument("--image-mode", choices=["pictures", "files"], default="pictures")
    parser.add_argument("--no-ocr", action="store_true", help="Text-only run; scanned content is marked for review")
    parser.add_argument("--fresh", action="store_true", help="Ignore previous checkpoints")
    parser.add_argument("--offline",action="store_true",help="Skip online ISBN title lookup")
    from inventory.engine import DEFAULT_WORKERS, MAX_WORKERS
    parser.add_argument('--workers',type=int,choices=range(1,MAX_WORKERS+1),default=DEFAULT_WORKERS,
                        help=f'Parallel book processes, 1-{MAX_WORKERS} (default: {DEFAULT_WORKERS})')
    args = parser.parse_args()
    if args.folder is None:
        from inventory.gui import launch
        launch()
        return 0
    from inventory.engine import run, Options
    try:
        rows, cancelled = run(args.folder, args.output,
                              Options(grouping=args.grouping, image_mode=args.image_mode, ocr=not args.no_ocr,online_titles=not args.offline,workers=args.workers),
                              lambda done, total, text: print(f"[{done}/{total}] {text}", flush=True),
                              resume=not args.fresh)
        print(f"{'Partial' if cancelled else 'Saved'}: {args.output.resolve()} ({len(rows)} books)")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
