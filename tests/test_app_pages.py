"""Every dashboard page must render without exception on the sample data (needs processed/ from one import)."""
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hmx.store import ingest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = str(Path(__file__).parent / "harness_app.py")


@pytest.fixture(scope="module", autouse=True)
def _ensure_import():
    """Pages need processed data. Use the existing cache; import raw_data once (about 3 minutes) if there is none."""
    raw = ROOT / "raw_data"
    if not raw.exists():
        pytest.skip("raw_data missing")
    if not list((ROOT / "processed" / "samples").glob("*/meta.json")):
        ingest(raw, ROOT / "processed")


@pytest.mark.parametrize("page", ["demo", "overview", "morph", "dist", "rel", "track", "early", "qc", "home", "plates", "inventory", "endpoints", "dqm"])
def test_page_renders(page, monkeypatch):
    monkeypatch.setenv("HMX_TEST_PAGE", page)
    at = AppTest.from_file(HARNESS, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
