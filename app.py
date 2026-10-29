"""PhaseCast - forecasting cell potency from early quantitative phase imaging.   Run:  streamlit run app.py

How this file works (streamlit re-runs it top to bottom on every click):
  1. set the page layout and browser-tab title,
  2. import one function per dashboard page from the hmx package,
  3. get_context(): draw the sidebar (logo, data location, import button, filters) and return the loaded data,
  4. register the pages (grouped into sections) and let st.navigation draw the menu and run the chosen page.
To add a page: write a function f(ctx) in hmx/page_*.py and add it to SECTIONS below.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="PhaseCast", page_icon="🌊", layout="wide", initial_sidebar_state="expanded")

from hmx.demo import demo_overview                                    # noqa: E402
from hmx.page_explore import distributions, morphology_explorer, relationships   # noqa: E402
from hmx.page_overview import overview, quality_control                          # noqa: E402
from hmx.page_phasecast import (data_quality_mapping, dataset_inventory, endpoint_preview, plate_mapper,   # noqa: E402
                                research_overview)
from hmx.page_tracking import early_window, tracking_explorer          # noqa: E402
from hmx.ui_common import get_context                                  # noqa: E402

# Sidebar + data: everything the pages need (filtered tables, folders) arrives in this one object.
ctx = get_context()

# Section -> {menu title: page function}. The first page of the first section is the landing page.
SECTIONS = {
    "Project": {"Research Overview": research_overview},
    "Data": {"Plate Mapper": plate_mapper, "Dataset Inventory": dataset_inventory, "Endpoint Data Preview": endpoint_preview,
             "Data Quality and Mapping": data_quality_mapping},
    "Analysis": {"Morphology Explorer": morphology_explorer, "Cell Population Distributions": distributions,
                 "Feature Relationships": relationships, "Cell Tracking Explorer": tracking_explorer,
                 "Early-Window Features": early_window},
    "Reference": {"Demo Overview": demo_overview, "Quality Control & Dictionary": quality_control,
                  "Dataset Overview (legacy)": overview},
}


def _page(name: str, fn, default: bool):
    """Wrap a page function (taking ctx) as a Streamlit page with a readable URL."""
    return st.Page(lambda f=fn, c=ctx: f(c), title=name, url_path=name.lower().replace(" & ", "-").replace(" ", "-"), default=default)


nav = st.navigation({sec: [_page(n, f, default=(sec == "Project" and i == 0)) for i, (n, f) in enumerate(pages.items())]
                     for sec, pages in SECTIONS.items()}, position="sidebar")
nav.run()
