"""Command line: `python -m hmx.cli ingest [--data-dir DIR] [--force]` (the dashboard does the same via a button)."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import DEFAULT_DATA_DIR, DEFAULT_PROCESSED_DIR, PROJECT_ROOT
from .store import ingest, write_dictionary


def main() -> None:
    """Command-line entry point: python -m hmx.cli ingest."""
    ap = argparse.ArgumentParser(description="HoloMonitor ingestion")
    ap.add_argument("command", choices=["ingest"])
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    ap.add_argument("--force", action="store_true", help="re-import even if files are unchanged")
    ap.add_argument("--only", nargs="*", help="only these sample ids (e.g. A1P6)")
    a = ap.parse_args()
    rep = ingest(a.data_dir, a.out_dir, force=a.force, only=a.only,
                 progress=lambda f, msg: print(f"[{f:5.0%}] {msg}"))
    write_dictionary(a.out_dir, extra_dirs=[PROJECT_ROOT / "docs"])
    print(rep.summary())
    for sid, err in rep.failed:
        print("FAILED", sid, err)


if __name__ == "__main__":
    main()
