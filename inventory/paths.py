"""Resource and writable paths for source and packaged Windows runs."""
import os
from pathlib import Path
import sys


FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
STATE_DIR = ((Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EPDF Inventory")
             if FROZEN else BUNDLE_ROOT / "reports")
REPORT_DIR = ((Path.home() / "Documents" / "EPDF Inventory")
              if FROZEN else BUNDLE_ROOT / "reports")
