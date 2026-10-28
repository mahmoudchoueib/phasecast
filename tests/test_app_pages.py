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
    if (ROOT / "A1P6-MOR.xlsx").exists():
        ingest(ROOT, ROOT / "processed")
    else:
        pytest.skip("sample data missing")


@pytest.mark.parametrize("page", ["demo", "overview", "morph", "dist", "rel", "track", "early", "qc"])
def test_page_renders(page, monkeypatch):
    monkeypatch.setenv("HMX_TEST_PAGE", page)
    at = AppTest.from_file(HARNESS, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
