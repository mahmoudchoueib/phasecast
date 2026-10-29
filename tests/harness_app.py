"""Streamlit AppTest harness: renders ONE page chosen through the HMX_TEST_PAGE environment variable."""
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hmx.demo import demo_overview                                      # noqa: E402
from hmx.page_explore import distributions, morphology_explorer, relationships   # noqa: E402
from hmx.page_overview import overview, quality_control                 # noqa: E402
from hmx.page_tracking import early_window, tracking_explorer           # noqa: E402
from hmx.page_phasecast import (data_quality_mapping, dataset_inventory, endpoint_preview, plate_mapper, research_overview)  # noqa: E402
from hmx.ui_common import get_context                                   # noqa: E402

PAGES = {"demo": demo_overview, "overview": overview, "morph": morphology_explorer, "dist": distributions,
         "rel": relationships, "track": tracking_explorer, "early": early_window, "qc": quality_control,
         "home": research_overview, "plates": plate_mapper, "inventory": dataset_inventory, "endpoints": endpoint_preview,
         "dqm": data_quality_mapping}
ctx = get_context()
PAGES[os.environ["HMX_TEST_PAGE"]](ctx)
