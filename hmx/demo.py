"""Demo Overview: five high-value analyses with observations computed from the loaded data.

Every sentence under 'Observation' is generated from numbers in the data; nothing is hard-coded about
biology. If a requested feature is unavailable a documented substitute is used (``substitution``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import spec
from .analysis import auto_bin_width_h, compare_windows, correlate, describe, timeseries, window_frame
from .ui_common import EXPLORATORY, Ctx, header, need_data, show_fig
from .viz import PALETTE, fig_box, fig_hist, fig_scatter, fig_timeseries, style


@dataclass
class DemoItem:
    title: str
    fig: go.Figure
    data: pd.DataFrame
    measures: str
    observation: str
    caution: str
    substitution: str = ""
    key: str = ""


def _n(x, p=3) -> str:
    """Format a number with 3 significant figures."""
    return f"{x:.{p}g}"


def _ts_item(ds, feature: str, key: str, title_prefix: str) -> DemoItem | None:
    """Build a time-series demo figure for one feature, with its computed observation text."""
    cells = ds.cells
    if feature not in cells:
        return None
    bin_h = auto_bin_width_h(ds.frames)
    ts = timeseries(cells, ds.frames, feature, "cell", "mean", "iqr", "frame", bin_h, None, None, 5)
    if ts.empty:
        return None
    fig = fig_timeseries(ts, feature, "mean", "iqr", True, True,
                         title=f"{title_prefix}: {spec.label_of(feature, False)} over imaging time")
    tmax = ts["bin_h"].max()
    w = min(6.0, tmax / 4)
    first = cells[cells["elapsed_h"] <= w][feature].astype(float)
    last = cells[cells["elapsed_h"] >= tmax - w][feature].astype(float)
    unit = spec.unit_of(feature) or ""
    u = f" {unit}" if unit else ""
    c = ts["center"]
    obs = (f"The per-frame mean {spec.label_of(feature, False).lower()} ranged from {_n(c.min())} to {_n(c.max())}{u} across {len(ts)} time bins. "
           f"Averaged over the first {w:g} h it was {_n(first.mean())}{u} (n = {len(first)} cell observations) and over the last {w:g} h "
           f"{_n(last.mean())}{u} (n = {len(last)}), a difference of {_n(last.mean() - first.mean())}{u} "
           f"({100 * (last.mean() - first.mean()) / first.mean():+.1f}%) in this sample.")
    return DemoItem(title=fig.layout.title.text, fig=fig, data=ts, key=key,
                    measures=f"Each point is the mean of the per-frame means of {spec.label_of(feature, False).lower()} ({unit or 'unitless'}) at that elapsed time; "
                             "the band is the average within-frame interquartile range, i.e. how much cells differ from each other in a frame.",
                    observation=obs,
                    caution="Descriptive. Cells are not followed individually here, so a change in the average can reflect different cells being "
                            "present (division, cells leaving the field), not cells changing. "
                            + ("The definition of this feature is not given in the export (requires confirmation). " if spec.status_of(feature) == spec.CONFIRM else "")
                            + "One position cannot be generalised to other wells or donors.")


def build_items(ds) -> list[DemoItem]:
    """Create the five demo analyses (with documented substitutions) from the loaded data."""
    items: list[DemoItem] = []
    cells = ds.cells
    # 1 - area vs time
    it = _ts_item(ds, "area_um2", "d1", "1")
    if it:
        items.append(it)
    # 2 - hull convexity vs time (substitute shape convexity if hull convexity is missing)
    feat2 = "hull_convexity" if "hull_convexity" in cells else ("shape_convexity" if "shape_convexity" in cells else None)
    if feat2:
        it = _ts_item(ds, feat2, "d2", "2")
        if it:
            if feat2 != "hull_convexity":
                it.substitution = "Hull convexity is not in these files; Shape convexity was used instead."
            items.append(it)
    # 3 - area distributions, early vs late window
    if "area_um2" in cells and len(cells):
        tmax = float(cells["elapsed_h"].max())
        w = min(6.0, tmax / 4)
        A = window_frame(cells, 0, w).assign(window=f"Early: 0–{w:g} h")
        B = window_frame(cells, tmax - w, tmax).assign(window=f"Late: {tmax - w:.0f}–{tmax:.0f} h")
        both = pd.concat([A, B], ignore_index=True)
        fig = fig_hist(both, "area_um2", "window", 40, True, title="3: Cell area distributions, early vs late window")
        cmp = compare_windows(A["area_um2"], B["area_um2"])
        dA, dB = describe(A["area_um2"]), describe(B["area_um2"])
        obs = (f"Early window: median {_n(dA['median'])} µm² (IQR {_n(dA['p25'])}–{_n(dA['p75'])}, n = {dA['n']}); late window: median "
               f"{_n(dB['median'])} µm² (IQR {_n(dB['p25'])}–{_n(dB['p75'])}, n = {dB['n']}). Median difference (late − early) "
               f"{_n(cmp.get('median_diff_b_minus_a', np.nan))} µm²; KS distance D = {_n(cmp.get('ks_D', np.nan))}; "
               f"Cliff's delta = {_n(cmp.get('cliffs_delta_b_vs_a', np.nan))}.")
        items.append(DemoItem("3: Cell area distributions, early vs late window", fig,
                              both[["sample_id", "frame_no", "cell_id", "elapsed_h", "window", "area_um2"]], key="d3",
                              measures="Density histogram of the area (µm²) of every segmented, non-edge cell observation inside two time windows.",
                              observation=obs,
                              caution="No p-value is given: observations are repeated measures from the same positions, not independent samples. "
                                      "Cell density differs between windows, which alone can change observed sizes (cell size is not corrected for crowding)."))
    # 4 - area vs perimeter
    if {"area_um2", "perimeter_um"} <= set(cells.columns):
        cor = correlate(cells, "area_um2", "perimeter_um")
        fig = fig_scatter(cells, "area_um2", "perimeter_um", "elapsed_h", True,
                          [h for h in ["elapsed_h", "sample_id", "position_id", "frame_no", "cell_id"] if h in cells],
                          title="4: Cell area vs perimeter, coloured by elapsed time")
        lt = pd.Series({"slope": np.polyfit(cells["area_um2"].astype(float), cells["perimeter_um"].astype(float), 1)[0]})
        obs = (f"Across {cor['n']:,} cell observations, perimeter and area are positively related (Pearson r = {_n(cor['pearson_r'])}, Spearman ρ = "
               f"{_n(cor['spearman_rho'])}); the linear fit slopes by {_n(lt['slope'])} µm of perimeter per µm² of area.")
        items.append(DemoItem("4: Cell area vs perimeter", fig, cells[["sample_id", "frame_no", "cell_id", "elapsed_h", "area_um2", "perimeter_um"]],
                              key="d4",
                              measures="Each point is one cell observation: its area (µm²) against the length of its outline (µm).",
                              observation=obs,
                              caution="A strong association is expected from geometry alone (larger shapes have longer outlines), so it is not a biological finding. "
                                      "The relevant question for later work is the deviation from this relationship (shape), which is not tested here. "
                                      "Points are repeated observations of the same cells."))
    # 5 - tracking speed / displacement; else substitute
    ts = ds.track_summary
    if len(ts) and "mean_speed_um_h" in ts:
        k = ts[ts["n_obs"] >= 3].copy()
        if len(k):
            fig = go.Figure(go.Scatter(x=k["mean_speed_um_h"], y=k["net_displacement_um"], mode="markers", text=k["track_id"],
                                       marker=dict(size=8, color=k["directness"], colorscale="Viridis", colorbar=dict(title="Directness"),
                                                   line=dict(width=0.5, color="#fff")),
                                       hovertemplate="%{text}<br>speed %{x:.1f} µm/h<br>net displacement %{y:.1f} µm<extra></extra>"))
            style(fig, "5: Tracked-cell speed vs net displacement", "Mean speed over track lifetime (µm/h)", "Net displacement (µm)", 480)
            s, d = k["mean_speed_um_h"], k["net_displacement_um"]
            obs = (f"{len(k)} tracks with ≥3 observations (of {len(ts)} tracks): median mean-speed {_n(s.median())} µm/h (IQR {_n(s.quantile(.25))}–{_n(s.quantile(.75))}), "
                   f"median net displacement {_n(d.median())} µm (IQR {_n(d.quantile(.25))}–{_n(d.quantile(.75))}), median directness {_n(k['directness'].median())}. "
                   f"{int(ts['divides'].sum())} divisions were recorded.")
            items.append(DemoItem("5: Tracked-cell speed vs net displacement", fig,
                                  k[["sample_id", "track_id", "n_obs", "duration_s", "mean_speed_um_h", "net_displacement_um", "directness"]], key="d5",
                                  measures="Each point is a tracked cell: its mean speed (accumulated distance ÷ lifetime) against the straight-line distance between its first and last position.",
                                  observation=obs,
                                  caution="Tracks end when cells divide, leave the field or disappear, so they differ in length and are not a random sample. "
                                          "Speeds are from cell centroids at ~30-min sampling and may underestimate true path length. Motility has not been related to fusion."))
    elif "optical_thickness_avg_um" in cells:
        it = _ts_item(ds, "optical_thickness_avg_um", "d5", "5 (substitute)")
        if it:
            it.substitution = "No tracking data are available for the selected positions; mean optical thickness over time is shown instead."
            items.append(it)
    return items


def demo_overview(ctx: Ctx) -> None:
    """PAGE: Demo Overview - shows the five analyses with what is measured, the observation and the caution."""
    header("Demo Overview", "Five analyses of the loaded data, generated automatically. Every observation is computed from the files - none is hard-coded.")
    if not need_data(ctx, "cells"):
        return
    ds = ctx.ds
    s = ds.samples
    st.success(f"Showing {len(s)} position(s): "
               f"{len(ds.frames):,} frames · {len(ds.cells):,} cell observations · {len(ds.track_summary):,} tracked cells · "
               f"{s['duration_h'].max():.1f} h of imaging. Donor: {', '.join(sorted(s['donor_id'].astype(str).unique()))}.")
    st.info(EXPLORATORY)
    items = build_items(ds)
    for it in items:
        st.markdown("---")
        st.subheader(it.title)
        a, b = st.columns([3, 2])
        with a:
            show_fig(it.fig, it.key, it.data, f"demo_{it.key}")
        with b:
            st.markdown(f"**What is measured.** {it.measures}")
            st.markdown(f"**Observation (from the data).** {it.observation}")
            st.markdown(f"**What cannot be inferred.** {it.caution}")
            if it.substitution:
                st.warning(f"Substitution: {it.substitution}")
    if not items:
        st.warning("None of the demo analyses could be built from the selected data.")

