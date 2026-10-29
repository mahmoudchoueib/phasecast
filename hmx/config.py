"""Locations. Defaults can be overridden in the sidebar or with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# PhaseCast keeps the raw HoloMonitor exports (read-only) in raw_data/; fall back to the project root.
_RAW = PROJECT_ROOT / "raw_data"
DEFAULT_DATA_DIR = Path(os.environ.get("HMX_DATA_DIR", _RAW if _RAW.exists() else PROJECT_ROOT))
DEFAULT_PROCESSED_DIR = Path(os.environ.get("HMX_PROCESSED_DIR", PROJECT_ROOT / "processed"))
DEFAULT_MANIFEST = Path(os.environ.get("HMX_MANIFEST", PROJECT_ROOT / "manifest.csv"))
# Editable donor/plate decisions (plates.csv, donor_map.csv, donor_aliases.csv, sample_resolution.csv ...)
DEFAULT_META_DIR = Path(os.environ.get("HMX_META_DIR", PROJECT_ROOT / "metadata"))
