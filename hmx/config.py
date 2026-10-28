"""Locations. Defaults can be overridden in the sidebar or with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path(os.environ.get("HMX_DATA_DIR", PROJECT_ROOT))
DEFAULT_PROCESSED_DIR = Path(os.environ.get("HMX_PROCESSED_DIR", PROJECT_ROOT / "processed"))
DEFAULT_MANIFEST = Path(os.environ.get("HMX_MANIFEST", PROJECT_ROOT / "manifest.csv"))
