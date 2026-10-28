"""HoloMonitor Cell Morphology and Tracking Explorer.   Run:  streamlit run app.py

How this file works (streamlit re-runs it top to bottom on every click):
  1. set the page layout,
  2. import one function per dashboard page from the hmx package,
  3. get_context(): draw the sidebar (data location, import button, filters) and return the filtered data,
  4. register the pages in the PAGES dict and let st.navigation draw the menu and run the chosen page.
To add a page: write a function f(ctx) in hmx/page_*.py and add it to PAGES below.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="HoloMonitor Explorer", page_icon="🔬", layout="wide", initial_sidebar_state="expanded")

from hmx.demo import demo_overview                                    # noqa: E402
from hmx.page_explore import distributions, morphology_explorer, relationships   # noqa: E402
from hmx.page_overview import overview, quality_control               # noqa: E402
from hmx.page_tracking import early_window, tracking_explorer          # noqa: E402
from hmx.ui_common import get_context                                  # noqa: E402

st.markdown(
    """<style>
    .block-container {padding-top: 2rem; max-width: 1500px;}
    [data-testid="stMetricValue"] {font-size: 1.6rem;}
    h1 {font-size: 2rem !important;}
    </style>""", unsafe_allow_html=True)

# Sidebar + data: everything the pages need (filtered tables, folders) arrives in this one object.
ctx = get_context()

# Menu order = dict order; the first entry is the landing page. Each value is a function taking ctx.
PAGES = {
    "Demo Overview": demo_overview,
    "Dataset Overview": overview,
    "Morphology Explorer": morphology_explorer,
    "Cell Population Distributions": distributions,
    "Feature Relationships": relationships,
    "Cell Tracking Explorer": tracking_explorer,
    "Early-Window Features": early_window,
    "Quality Control & Dictionary": quality_control,
}
# Build the sidebar menu; each page is a tiny lambda that calls the page function with ctx.
nav = st.navigation([st.Page(lambda f=f, c=ctx: f(c), title=name, url_path=name.lower().replace(" & ", "-").replace(" ", "-"),
                             default=(i == 0)) for i, (name, f) in enumerate(PAGES.items())], position="sidebar")
nav.run()

