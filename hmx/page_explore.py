"""Pages 2-4: Morphology Explorer, Cell Population Distributions, Feature Relationships."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from . import spec
from .analysis import (WINDOW_PRESETS_H, auto_bin_width_h, compare_windows, corr_matrix, correlate, describe,
                       heterogeneity, linear_trend, timeseries, window_frame)
from .ui_common import EXPLORATORY, Ctx, csv_bytes, header, need_data, show_fig
from .viz import fig_box, fig_corr_heatmap, fig_heterogeneity, fig_hist, fig_scatter, fig_timeseries

FRAME_LEVEL_FEATURES = ["confluence_pct", "n_cells"]


def _cell_feats(df: pd.DataFrame) -> list[str]:
    """Cell-level features present in the table, in dictionary order."""
    return [f.internal for f in spec.CELL_FEATURES if f.internal in df.columns]


def morphology_explorer(ctx: Ctx) -> None:
    """PAGE: Morphology Explorer - a feature against elapsed time with statistic, band, smoothing, weighting and grouping controls."""
    header("Morphology Explorer", "Population-level morphology against elapsed imaging time.")
    if not need_data(ctx, "cells"):
        return
    ds = ctx.ds
    cells, frames = ds.cells, ds.frames
    cfeats = _cell_feats(cells)
    ffeats = [f for f in FRAME_LEVEL_FEATURES if f in frames]
    options = [("cell", f) for f in cfeats] + [("frame", f) for f in ffeats]
    default = next((i for i, (_, f) in enumerate(options) if f == "area_um2"), 0)
    c = st.columns([2, 1, 1, 1])
    lvl, feat = c[0].selectbox("Feature", options, index=default,
                               format_func=lambda o: f"{spec.label_of(o[1])}" + ("  [frame-level]" if o[0] == "frame" else ""))
    stat = c[1].radio("Statistic", ["mean", "median"], horizontal=True)
    band = c[2].radio("Spread band", ["none", "sd", "iqr"], horizontal=True, format_func={"none": "None", "sd": "± SD", "iqr": "IQR"}.get)
    weighting = c[3].radio("Weighting", ["frame", "pooled"], horizontal=True, disabled=(lvl == "frame"),
                           format_func={"frame": "Equal frame", "pooled": "Pooled cells"}.get)
    with st.expander("Equal-frame vs pooled-cell weighting - what is the difference?"):
        st.markdown(
            "- **Equal-frame weighting**: the statistic is computed *within each frame first*, then averaged across the frames "
            "(and positions) in each time bin. Every frame counts equally, so a frame with 3 cells carries the same weight as one with 30. "
            "The band shows the *average within-frame* spread.\n"
            "- **Pooled-cell weighting**: all cell observations falling in a time bin are pooled and the statistic is taken across them. "
            "Frames with more cells (and positions with more cells) count more. The band is the spread of the pooled cells.\n\n"
            "With one position and one frame per time bin, the two choices give the same mean; they diverge when frames contain very different "
            "numbers of cells or when several positions are combined. For *medians* they generally differ. Frame-level features "
            "(confluence, number of cells) have one value per frame, so weighting does not apply.")
    tmax = float(max(frames["elapsed_h"].max(), cells["elapsed_h"].max()))
    c = st.columns([2, 1, 1, 1])
    t_range = c[0].slider("Time range (h)", 0.0, round(tmax, 1), (0.0, round(tmax, 1)), step=0.5)
    group_by = c[1].selectbox("Show", [None, "sample_id", "donor_id", "well_id", "position_id"],
                              format_func={None: "All selected pooled", "sample_id": "Each position", "donor_id": "By donor",
                                           "well_id": "By well", "position_id": "By position id"}.get)
    smooth = c[2].radio("Values", ["raw", "smoothed"], horizontal=True)
    k = c[3].slider("Smoothing window (frames)", 3, 15, 5, step=2, disabled=smooth == "raw")
    bin_h = auto_bin_width_h(frames)
    ts = timeseries(cells, frames, feat, lvl, stat, band, weighting, bin_h, t_range, group_by, k if smooth == "smoothed" else 1)
    if ts.empty:
        st.warning("No data in this range.")
        return
    fig = fig_timeseries(ts, feat, stat, band, smooth == "smoothed", show_raw=True)
    show_fig(fig, "morph_ts", ts, f"{feat}_timeseries")
    n_pos = ds.samples["sample_id"].nunique()
    st.caption(f"{stat.title()} per {bin_h:g} h bin · {n_pos} position(s) selected · "
               f"{'equal frame' if (weighting == 'frame' and lvl == 'cell') else 'pooled cell' if lvl == 'cell' else 'per-frame'} weighting. "
               "Hover shows how many observations and frames contribute to each point.")
    st.info(EXPLORATORY)
    if spec.status_of(feat) == spec.CONFIRM:
        st.warning(f"**{spec.label_of(feat, False)}**: its definition is not stated in the HoloMonitor export (see Data Dictionary → "
                   "'requires confirmation'). Interpret changes cautiously.")


# ------------------------------------------------------------------------------------------------
def _window_picker(label: str, tmax: float, key: str, default_preset: str, default_custom=(0.0, 12.0)) -> tuple[float, float]:
    """Widget for choosing a time window: preset 0-X h or custom start/end."""
    presets = {f"0–{h} h": (0.0, float(h)) for h in WINDOW_PRESETS_H}
    options = list(presets) + ["Custom"]
    choice = st.selectbox(label, options, index=options.index(default_preset), key=f"{key}_p")
    if choice != "Custom":
        return presets[choice]
    a = st.number_input("Start (h)", 0.0, tmax, default_custom[0], 0.5, key=f"{key}_s")
    b = st.number_input("End (h)", 0.0, tmax, min(default_custom[1], tmax), 0.5, key=f"{key}_e")
    return float(min(a, b)), float(max(a, b))


def distributions(ctx: Ctx) -> None:
    """PAGE: Cell Population Distributions - compare two time windows (histogram, box, violin, statistics, effect sizes) and heterogeneity over time."""
    header("Cell Population Distributions", "Distribution of a cell-level feature inside time windows, and how heterogeneity evolves.")
    if not need_data(ctx, "cells"):
        return
    cells = ctx.ds.cells
    tmax = float(cells["elapsed_h"].max())
    feat = st.selectbox("Cell-level feature", _cell_feats(cells), index=_cell_feats(cells).index("area_um2") if "area_um2" in cells else 0,
                        format_func=spec.label_of)
    c = st.columns(2)
    with c[0]:
        st.markdown("**Window A**")
        wa = _window_picker("Window A", tmax, "wa", "0–6 h")
    with c[1]:
        st.markdown("**Window B**")
        wb = _window_picker("Window B", tmax, "wb", "Custom", (max(0.0, tmax - 6.0), tmax))
    A = window_frame(cells, *wa).assign(window=f"A: {wa[0]:g}–{wa[1]:g} h")
    B = window_frame(cells, *wb).assign(window=f"B: {wb[0]:g}–{wb[1]:g} h")
    both = pd.concat([A, B], ignore_index=True)
    if both.empty:
        st.warning("No cell observations fall in these windows.")
        return
    st.info(EXPLORATORY + " Windows may overlap (e.g. '0–12 h' contains '0–6 h'); the same cells can appear in several windows.")
    tabs = st.tabs(["Histogram", "Box plot", "Violin plot", "Summary statistics", "Window comparison", "Heterogeneity over time"])
    with tabs[0]:
        c = st.columns(2)
        bins = c[0].slider("Bins", 10, 100, 40)
        dens = c[1].checkbox("Normalise to density (compare windows of different size)", True)
        show_fig(fig_hist(both, feat, "window", bins, dens), "dist_hist", both[["sample_id", "frame_no", "cell_id", "elapsed_h", "window", feat]], f"{feat}_hist")
    with tabs[1]:
        show_fig(fig_box(both, feat, "window"), "dist_box", None, f"{feat}_box")
    with tabs[2]:
        show_fig(fig_box(both, feat, "window", violin=True), "dist_violin", None, f"{feat}_violin")
    with tabs[3]:
        rows = []
        for name, d in both.groupby("window"):
            rows.append(dict(window=name, **describe(d[feat])))
        t = pd.DataFrame(rows)
        t["frames"] = both.groupby("window")[["sample_id", "frame_no"]].apply(lambda x: x.drop_duplicates().shape[0]).values
        st.dataframe(t.rename(columns={"std": "SD", "cv": "CV", "iqr": "IQR", "p10": "10th pct", "p25": "25th pct", "p75": "75th pct",
                                       "p90": "90th pct", "n": "n cell obs."}), hide_index=True, width="stretch")
        st.caption(f"Units: {spec.unit_of(feat) or 'none stated'}. n counts cell *observations*, not independent cells.")
        st.download_button("Summary (CSV)", csv_bytes(t), f"{feat}_window_summary.csv", "text/csv")
    with tabs[4]:
        cmp = compare_windows(A[feat], B[feat])
        if cmp:
            c = st.columns(4)
            c[0].metric("Median B − A", f"{cmp['median_diff_b_minus_a']:.4g}")
            c[1].metric("Mean B − A", f"{cmp['mean_diff_b_minus_a']:.4g}")
            c[2].metric("KS distance D", f"{cmp['ks_D']:.3f}", help="Largest gap between the two empirical distributions (0 = identical).")
            c[3].metric("Cliff's delta (B vs A)", f"{cmp['cliffs_delta_b_vs_a']:.3f}",
                        help="−1…+1; positive: values in B tend to be larger than in A.")
            st.caption("Descriptive effect sizes only. **No p-values are shown**: cell observations within and across frames are not "
                       "independent, so a conventional test would overstate certainty. Windows with overlapping data share observations.")
        else:
            st.info("Need at least two observations in each window.")
    with tabs[5]:
        c = st.columns(2)
        hb = c[0].slider("Bin width (h)", 0.5, 12.0, 3.0, 0.5)
        hr = c[1].slider("Time range (h)", 0.0, round(tmax, 1), (0.0, round(tmax, 1)), 0.5, key="het_range")
        h = heterogeneity(cells, feat, hb, hr)
        if len(h) >= 2:
            show_fig(fig_heterogeneity(h, feat), "het", h, f"{feat}_heterogeneity")
            tr = linear_trend(h["bin_h"], h["cv"])
            st.caption(f"Linear trend of CV across bins: slope {tr['slope']:.4g} per h (r = {tr['r']:.2f}, {tr['n']} bins) - "
                       "descriptive only. CV = SD/mean is only meaningful for strictly positive, ratio-scale features; "
                       "IQR/median is the more robust companion. Bins with few cells give noisy estimates (see n in the table).")
            st.dataframe(h, hide_index=True, width="stretch")
        else:
            st.info("Not enough bins in the selected range.")


# ------------------------------------------------------------------------------------------------
def _linked_table(ds) -> pd.DataFrame:
    """Morphology cells joined to tracking observations through the INFERRED link (see tracks.link_to_morphology)."""
    o = ds.track_obs
    if o.empty or ds.cells.empty or "morph_cell_id" not in o:
        return pd.DataFrame()
    lk = o[o["link_status"].isin(["linked_validated", "linked_unvalidated"])]
    if lk.empty:
        return pd.DataFrame()
    keep = ["sample_id", "frame_no", "morph_cell_id", "track_id", "family", "migration_um", "motility_um", "motility_speed_um_h",
            "migration_directness", "age_s", "link_status"]
    lk = lk[[c for c in keep if c in lk]].copy()
    lk["morph_cell_id"] = lk["morph_cell_id"].astype("Int32")
    cells = ds.cells.copy()
    cells["cell_id"] = cells["cell_id"].astype("Int32")
    ren = {c: f"trk_{c}" for c in ["migration_um", "motility_um", "motility_speed_um_h", "migration_directness", "age_s"] if c in lk}
    m = cells.merge(lk.rename(columns=ren), left_on=["sample_id", "frame_no", "cell_id"], right_on=["sample_id", "frame_no", "morph_cell_id"], how="inner")
    return m


def relationships(ctx: Ctx) -> None:
    """PAGE: Feature Relationships - scatter, Pearson/Spearman, trend line, heatmap; optional inferred morphology+tracking link."""
    header("Feature Relationships", "Exploratory associations between two features, with Pearson and Spearman correlation.")
    if not need_data(ctx):
        return
    ds = ctx.ds
    choices = {}
    if len(ds.cells):
        choices["Cell morphology (one row per cell observation)"] = ds.cells
    if len(ds.frames):
        choices["Frame level (one row per frame)"] = ds.frames
    if len(ds.track_obs):
        choices["Tracking observations (one row per tracked cell and time)"] = ds.track_obs
    if len(ds.track_summary):
        choices["Track summaries (one row per tracked cell)"] = ds.track_summary
    linked = _linked_table(ds)
    if len(linked):
        choices["Morphology + tracking, linked per cell (INFERRED link)"] = linked
    if not choices:
        st.warning("No data available.")
        return
    name = st.selectbox("Dataset", list(choices))
    df = choices[name]
    if "Linked" in name or "linked" in name:
        n_all = len(ds.track_obs)
        st.warning(
            f"**Inferred link.** The morphology `Cell ID` is a per-frame label and does not equal the tracking cell name, so the two exports share "
            f"no identifier. Rows are joined only where frame, Area and Optical volume are *exactly* equal (unique matches), and each family's links "
            f"are validated against the tracking positions. {len(linked):,} of {n_all:,} tracking observations ({100 * len(linked) / n_all:.0f}%) "
            "linked; unlinked ones are presumably edge cells, which the morphology export excludes (not confirmed).")
    elif len(ds.cells) and len(ds.track_obs):
        st.caption("Morphology and tracking are analysed separately by default. The linked dataset (if available) is offered above as an explicit option.")
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not c.endswith("_id") and c not in ("frame_no", "frame_id")]
    known = [c for c in num if c in {f.internal for f in spec.ALL_FEATURES}]
    pref = ["elapsed_h", "elapsed_s"] + known
    opts = list(dict.fromkeys([c for c in pref if c in num] + num))
    c = st.columns([1, 1, 1, 1])
    xi = opts.index("area_um2") if "area_um2" in opts else 0
    yi = opts.index("perimeter_um") if "perimeter_um" in opts else min(1, len(opts) - 1)
    x = c[0].selectbox("X feature", opts, index=xi, format_func=spec.label_of)
    y = c[1].selectbox("Y feature", opts, index=yi, format_func=spec.label_of)
    color_opts = ["None", "Elapsed time", "Donor", "Position", "Well"]
    color = c[2].selectbox("Colour by", color_opts)
    trend = c[3].checkbox("Regression trend line", True)
    if "elapsed_h" in df and df["elapsed_h"].notna().any():
        tmax = float(df["elapsed_h"].max())
        tr = st.slider("Time range (h)", 0.0, round(tmax, 1), (0.0, round(tmax, 1)), 0.5, key="rel_t")
        df = df[(df["elapsed_h"] >= tr[0]) & (df["elapsed_h"] <= tr[1])]
    elif "elapsed_s" in df:
        df = df.copy()
        df["elapsed_h"] = df["elapsed_s"] / 3600
    cmap = {"Elapsed time": "elapsed_h", "Donor": "donor_id", "Position": "position_id", "Well": "well_id"}
    ccol = cmap.get(color)
    if ccol and ccol not in df:
        ccol = None
    hover = [h for h in ["elapsed_h", "sample_id", "position_id", "frame_no", "cell_id", "track_id", "trk_track_id"] if h in df]
    if ccol and ccol != "elapsed_h":
        df = df.assign(**{ccol: df[ccol].astype(str)})
    if df.empty or x not in df or y not in df:
        st.warning("Nothing to plot.")
        return
    cor = correlate(df, x, y)
    m = st.columns(4)
    m[0].metric("Observations (n)", f"{cor['n']:,}")
    m[1].metric("Pearson r", "—" if np.isnan(cor["pearson_r"]) else f"{cor['pearson_r']:.3f}")
    m[2].metric("Spearman ρ", "—" if np.isnan(cor["spearman_rho"]) else f"{cor['spearman_rho']:.3f}")
    lt = linear_trend(df[x], df[y])
    m[3].metric("OLS slope", "—" if np.isnan(lt["slope"]) else f"{lt['slope']:.4g}", help=f"{spec.label_of(y)} per unit of {spec.label_of(x)}")
    fig = fig_scatter(df, x, y, ccol, trend, hover)
    keep = [c for c in hover + [x, y] if c in df]
    show_fig(fig, "rel_scatter", df[list(dict.fromkeys(keep))], f"{y}_vs_{x}")
    st.info(EXPLORATORY + " The correlations above treat every row as independent, which it is not; they describe the sample, not the population. "
            "Geometrically related features (e.g. area and perimeter) are expected to correlate by construction.")
    st.subheader("Correlation heatmap")
    default = [f for f in spec.HEADLINE_CELL_FEATURES if f in num]
    sel = st.multiselect("Features", opts, default=default or opts[:6], format_func=spec.label_of)
    method = st.radio("Method", ["spearman", "pearson"], horizontal=True)
    if len(sel) >= 2:
        cm = corr_matrix(df, sel, method)
        show_fig(fig_corr_heatmap(cm, method), "rel_heat", cm.reset_index(names="feature"), f"correlation_{method}")
    else:
        st.info("Select at least two features.")
