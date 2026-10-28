"""Pages 5-6: Cell Tracking Explorer and Early-Window Feature Engineering."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import spec
from .features import (CUTOFFS_H, STATS, aggregate_to_donor, default_feature_sets, early_window_table, join_endpoints, to_wide)
from .tracks import add_steps, lineage_checks
from .ui_common import EXPLORATORY, Ctx, csv_bytes, header, need_data, parquet_bytes, show_fig
from .viz import PALETTE, fig_distribution, fig_trajectories, style


def tracking_explorer(ctx: Ctx) -> None:
    """PAGE: Cell Tracking Explorer - trajectories, motility statistics, divisions/lineage and the track table for one position."""
    header("Cell Tracking Explorer", "Trajectories, motility, divisions and lineage from the tracking export.")
    if not need_data(ctx, "track_obs"):
        return
    ds = ctx.ds
    sids = list(ds.track_summary["sample_id"].astype(str).unique())
    sid = st.selectbox("Imaging position (tracks are never mixed across positions)", sids) if len(sids) > 1 else sids[0]
    ts = ds.track_summary[ds.track_summary["sample_id"].astype(str) == sid].copy()
    obs = ds.track_obs[ds.track_obs["sample_id"].astype(str) == sid].copy()
    has_xy = {"rel_x_um", "rel_y_um"} <= set(obs.columns) and obs["rel_x_um"].notna().any()
    ts["duration_h"] = ts["duration_s"] / 3600
    ts["cycle_length_h"] = ts["cycle_length_s"] / 3600
    link = obs["link_status"].value_counts() if "link_status" in obs else pd.Series(dtype=int)
    n_link = int(link.get("linked_validated", 0) + link.get("linked_unvalidated", 0))

    c = st.columns(5)
    c[0].metric("Tracked cells", len(ts))
    c[1].metric("Families", ts["family"].nunique(), help="A family = founder cell + all descendants (one 'Cell n' worksheet each).")
    c[2].metric("Divisions", int(ts["divides"].sum()))
    c[3].metric("Complete cell cycles", int(ts["cycle_complete"].sum()))
    cyc = ts["cycle_length_h"].dropna()
    c[4].metric("Mean cell-cycle length", f"{cyc.mean():.2f} h" if len(cyc) else "—")

    if not has_xy:
        st.warning("This tracking file has no coordinate data (no per-cell sheets). Trajectories cannot be drawn; only movement statistics "
                   "from the exported feature matrices are shown. No coordinates were fabricated.")
    tabs = st.tabs(["Trajectories", "Motility statistics", "Divisions & lineage", "Track table"])

    with tabs[0]:
        if not has_xy:
            st.info("Coordinates unavailable.")
        else:
            c = st.columns([2, 1, 1])
            fams = sorted(ts["family"].unique(), key=lambda s: int(s.split()[-1]) if s.split()[-1].isdigit() else 0)
            pick_mode = c[0].radio("Select by", ["Longest tracks", "Families", "Individual cell IDs"], horizontal=True)
            coord = c[1].selectbox("Coordinates", ["Start at origin", "Family frame (lineage-continuous)", "Imaging field (absolute)"],
                                   help="Origin: each track begins at (0,0). Family: daughters continue from the parent's last position. "
                                        "Field: position inside the 566 µm image, available for families whose position could be anchored via the "
                                        "linked morphology cells.")
            color_by = c[2].radio("Colour", ["track", "time"], horizontal=True)
            min_obs = st.slider("Minimum observations per track", 1, int(ts["n_obs"].max()), 5)
            pool = ts[ts["n_obs"] >= min_obs]
            if pick_mode == "Longest tracks":
                n = st.slider("Number of tracks", 1, min(40, max(1, len(pool))), min(8, max(1, len(pool))))
                chosen = list(pool.sort_values("n_obs", ascending=False).head(n)["track_id"])
            elif pick_mode == "Families":
                fsel = st.multiselect("Families", fams, default=fams[:1])
                chosen = list(pool[pool["family"].isin(fsel)]["track_id"])
            else:
                chosen = st.multiselect("Cell IDs", list(pool["track_id"]), default=list(pool.sort_values("n_obs", ascending=False)["track_id"].head(4)))
            sub = obs[obs["track_id"].isin(chosen)].copy()
            if sub.empty:
                st.info("Select at least one track.")
            else:
                if coord.startswith("Start"):
                    first = sub.sort_values("elapsed_s").groupby("track_id")[["rel_x_um", "rel_y_um"]].transform("first")
                    sub["x"], sub["y"] = (sub["rel_x_um"] - first["rel_x_um"]).astype(float), (sub["rel_y_um"] - first["rel_y_um"]).astype(float)
                    mode = "origin"
                elif coord.startswith("Family"):
                    sub["x"], sub["y"], mode = sub["fam_x_um"].astype(float), sub["fam_y_um"].astype(float), "family"
                else:
                    sub["x"], sub["y"], mode = sub["abs_x_um"].astype(float), sub["abs_y_um"].astype(float), "field"
                    miss = sub.groupby("track_id")["x"].apply(lambda s: s.isna().all())
                    if miss.any():
                        st.warning(f"{int(miss.sum())} selected track(s) have no absolute position (their family could not be anchored to the image) and are not drawn.")
                fov = None
                if "image_width_um" in ds.frames:
                    fr = ds.frames[ds.frames["sample_id"].astype(str) == sid]
                    fov = (float(fr["image_width_um"].iloc[0]), float(fr["image_height_um"].iloc[0]))
                fig = fig_trajectories(sub, "x", "y", mode, color_by, fov)
                show_fig(fig, "traj", sub[["track_id", "family", "elapsed_s", "x", "y", "end_status"]], f"trajectories_{sid}")
                st.caption("○ start, ■ end of each track. Units: µm (physical, from the exported positions; pixel scale in the morphology export is "
                           "0.55332 µm/pxl). Positions are cell centroids; stage drift is not corrected by this software.")
                st.subheader("Selected tracks compared")
                comp_cols = ["track_id", "n_obs", "duration_h", "accumulated_distance_um", "net_displacement_um", "directness",
                             "mean_speed_um_h", "max_step_speed_um_h", "end_status", "divides"]
                cmp = ts[ts["track_id"].isin(chosen)][[c for c in comp_cols if c in ts]]
                st.dataframe(cmp, hide_index=True, width="stretch")
                st.download_button("Selected tracks (CSV)", csv_bytes(cmp), f"selected_tracks_{sid}.csv", "text/csv")
                steps = add_steps(sub)
                fig2 = go.Figure()
                for i, t in enumerate(chosen):
                    d = steps[steps["track_id"] == t]
                    fig2.add_trace(go.Scatter(x=d["elapsed_s"] / 3600, y=d["step_speed_um_h"], mode="lines+markers", name=t,
                                              line=dict(color=PALETTE[i % len(PALETTE)], width=1.8), marker=dict(size=4)))
                style(fig2, "Step speed of the selected tracks", "Elapsed imaging time (h)", "Step speed (µm/h)", 380)
                show_fig(fig2, "stepspeed", steps[["track_id", "elapsed_s", "step_um", "step_speed_um_h"]], f"step_speed_{sid}", png_button=False)
                st.caption("Step speed = distance between consecutive positions ÷ time between frames (recomputed here). The exported 'Motility speed' "
                           "is a windowed mean speed, not a per-step value (see Data Dictionary).")

    with tabs[1]:
        min_obs = st.slider("Minimum observations per track for kinematic statistics", 2, 20, 3, key="kin_min",
                            help="Speed / displacement of a 1-2 frame track are dominated by noise.")
        k = ts[ts["n_obs"] >= min_obs]
        if k.empty or "mean_speed_um_h" not in k:
            st.info("No kinematic data available for these settings.")
        else:
            c = st.columns(4)
            c[0].metric("Tracks analysed", len(k), help=f"{len(ts) - len(k)} shorter tracks excluded from this tab only.")
            c[1].metric("Median speed", f"{k['mean_speed_um_h'].median():.1f} µm/h")
            c[2].metric("Median net displacement", f"{k['net_displacement_um'].median():.1f} µm")
            c[3].metric("Median directness", f"{k['directness'].median():.2f}")
            a, b = st.columns(2)
            with a:
                show_fig(fig_distribution(k, "mean_speed_um_h", "Mean speed per track", "Mean speed (µm/h)"), "sp_hist", k[["track_id", "mean_speed_um_h"]], "track_speed")
            with b:
                show_fig(fig_distribution(k, "net_displacement_um", "Net displacement per track", "Net displacement (µm)"), "nd_hist", k[["track_id", "net_displacement_um"]], "track_displacement")
            a, b = st.columns(2)
            with a:
                fig = go.Figure(go.Scatter(x=k["accumulated_distance_um"], y=k["net_displacement_um"], mode="markers", text=k["track_id"],
                                           marker=dict(color=k["duration_h"], colorscale="Viridis", size=7, colorbar=dict(title="Duration (h)")),
                                           hovertemplate="%{text}<br>accumulated %{x:.1f} µm<br>net %{y:.1f} µm<extra></extra>"))
                m = float(k["accumulated_distance_um"].max())
                fig.add_trace(go.Scatter(x=[0, m], y=[0, m], mode="lines", line=dict(dash="dash", color="#888"), name="net = accumulated (directness 1)"))
                style(fig, "Net displacement vs accumulated distance", "Accumulated distance (µm)", "Net displacement (µm)", 420)
                show_fig(fig, "nd_vs_acc", k[["track_id", "accumulated_distance_um", "net_displacement_um", "duration_h"]], "displacement_vs_distance")
            with b:
                show_fig(fig_distribution(k, "directness", "Migration directness per track", "Directness (net ÷ accumulated; 1 = straight line)"), "dir_hist", k[["track_id", "directness"]], "track_directness")
            st.caption("Per-track values are averaged over each track's own lifetime. Tracks end when a cell migrates out of frame, divides or disappears, "
                       "so long-lived tracks are not a random sample of cells. " + EXPLORATORY)

    with tabs[2]:
        lc = lineage_checks(ts)
        st.markdown("**How divisions are represented (validated before use)**")
        st.markdown(
            f"- A track's last row carries `End status`; **`Divides`** marks a division ({lc['divisions']} here).\n"
            f"- Daughters are separate tracks named `<parent>-1` / `<parent>-2`; {lc['n_born_by_division']} tracks are daughters. "
            f"Parents missing from the file: **{lc['parent_missing']}**; parents not marked 'Divides': **{lc['parent_not_marked_divides']}**; "
            f"dividing tracks without exactly two daughters: **{lc['divisions_without_two_daughters']}**.\n"
            "- A daughter's first observation is one frame after the parent's last. **Cell-cycle length** = the daughters' first appearance "
            "− the cell's own first appearance, defined only for cells born by division that themselves divide (founder cells and cells still "
            "dividing after the end of imaging have no complete cycle). This reproduces the software's own 'Avg. cell cycle length' (see QC).\n"
            "- Because divisions are detected by tracking, missed or wrongly linked tracks would bias these counts.")
        c = st.columns(2)
        dv = ts[ts["divides"]]
        with c[0]:
            if len(dv):
                x = np.sort(dv["division_time_s"].to_numpy() / 3600)
                fig = go.Figure(go.Scatter(x=x, y=np.arange(1, len(x) + 1), mode="lines", line=dict(shape="hv", width=2.5, color=PALETTE[0]),
                                           hovertemplate="%{x:.1f} h: %{y} divisions<extra></extra>"))
                style(fig, "Cumulative divisions observed", "Elapsed imaging time (h)", "Cumulative divisions", 400)
                show_fig(fig, "cumdiv", pd.DataFrame({"division_time_h": x, "cumulative": np.arange(1, len(x) + 1)}), f"cumulative_divisions_{sid}")
            else:
                st.info("No divisions recorded.")
        with c[1]:
            if len(cyc):
                show_fig(fig_distribution(ts.dropna(subset=["cycle_length_h"]), "cycle_length_h", "Complete cell-cycle lengths", "Cell-cycle length (h)", "Number of cells", 20),
                         "cycle", ts.dropna(subset=["cycle_length_h"])[["track_id", "cycle_length_h"]], f"cell_cycle_{sid}")
                st.caption(f"{len(cyc)} complete cycles; median {cyc.median():.2f} h, IQR {cyc.quantile(.25):.2f}–{cyc.quantile(.75):.2f} h. "
                           "Cycles that would finish after imaging ended are unobservable, so the distribution is biased towards short cycles.")
            else:
                st.info("No complete cell cycles.")
        st.markdown("**End status of tracks**")
        es = ts["end_status"].fillna("").replace("", "(empty - not recorded)").value_counts().rename_axis("end_status").reset_index(name="tracks")
        st.dataframe(es, hide_index=True, width="stretch")
        fam = ts.groupby("family").agg(tracks=("track_id", "size"), divisions=("divides", "sum"),
                                       max_generation=("generation", "max"), first_frame=("start_frame", "min")).reset_index()
        st.markdown("**Cell families**")
        st.dataframe(fam.sort_values("tracks", ascending=False), hide_index=True, width="stretch")

    with tabs[3]:
        st.dataframe(ts, hide_index=True, width="stretch")
        st.download_button("Track summaries (CSV)", csv_bytes(ts), f"track_summary_{sid}.csv", "text/csv")
        st.caption(f"Linked to morphology (inferred, exact Area+Optical volume match): {n_link} of {len(obs)} observations. "
                   "`exported_*` columns are the software's final cumulative values; the other kinematic columns are recomputed from positions.")


# ------------------------------------------------------------------------------------------------
@st.cache_data(show_spinner="Computing early-window features…", max_entries=8)
def _early_table(_ds, key: tuple, cutoffs, cell_feats, frame_feats, traj) -> pd.DataFrame:
    """Cached wrapper around early_window_table (the leading underscore stops Streamlit hashing the big dataset)."""
    return early_window_table(_ds, list(cutoffs), list(cell_feats), list(frame_feats), traj)


def early_window(ctx: Ctx) -> None:
    """PAGE: Early-Window Feature Engineering - per position and cutoff feature table with CSV/Parquet export and donor/endpoint hooks."""
    header("Early-Window Feature Engineering",
           "Per-position feature summaries that use **only** observations up to a chosen cutoff - the input for later prediction work.")
    if not need_data(ctx):
        return
    ds = ctx.ds
    st.info("Each row is one imaging position at one cutoff T. Only frames, cells and tracking observations with elapsed time ≤ T are used "
            "(`window_max_elapsed_h` shows the latest observation actually used). No fusion-index information is used here.")
    dc, df_ = default_feature_sets(ds.cells, ds.frames)
    c = st.columns([2, 3, 3])
    cutoffs = c[0].multiselect("Observation cutoffs (h)", CUTOFFS_H, default=[6, 12, 24])
    cell_opts = [f.internal for f in spec.CELL_FEATURES if f.internal in ds.cells]
    cell_sel = c[1].multiselect("Cell-level features (pooled cell observations)", cell_opts, default=dc, format_func=spec.label_of)
    frame_opts = list(dict.fromkeys([f for f in ["confluence_pct", "n_cells"] if f in ds.frames] + cell_opts))
    frame_sel = c[2].multiselect("Frame-level features (per-frame values; trend & early-vs-late)", frame_opts, default=df_, format_func=spec.label_of)
    traj = st.checkbox("Include trajectory-level features (from tracking, truncated at the cutoff)", value=len(ds.track_obs) > 0,
                       disabled=ds.track_obs.empty)
    if not cutoffs:
        st.info("Choose at least one cutoff.")
        return
    key = (tuple(ds.samples["sample_id"]), tuple(sorted(ds.samples.get("hash_morphology", pd.Series(dtype=str)).fillna("").tolist())))
    table = _early_table(ds, key, tuple(sorted(cutoffs)), tuple(cell_sel), tuple(frame_sel), traj)
    if table.empty:
        st.warning("No features could be computed.")
        return
    c = st.columns(4)
    c[0].metric("Rows (position × cutoff)", len(table))
    c[1].metric("Feature columns", sum(1 for x in table.columns if "__" in x))
    c[2].metric("Windows complete", f"{int(table['window_complete'].sum())}/{len(table)}")
    c[3].metric("Max time used", f"{table['window_max_elapsed_h'].max():.2f} h")
    if (~table["window_complete"]).any():
        st.warning("Some windows are incomplete (the position was imaged for less than the cutoff, or has gaps); see `window_complete` / "
                   "`window_truncated_by_data_end`. Those rows summarise whatever was available - not the full window.")

    level = st.radio("Feature level to display", ["all", "cell__", "frame__", "traj__"], horizontal=True,
                     format_func={"all": "All levels", "cell__": "Cell-level (pooled cells)", "frame__": "Frame-level", "traj__": "Trajectory-level"}.get)
    meta_cols = [c for c in table.columns if "__" not in c]
    shown = table if level == "all" else table[meta_cols + [c for c in table.columns if c.startswith(level)]]
    fmt = st.radio("Table layout", ["long", "wide"], horizontal=True, format_func={"long": "One row per position × cutoff", "wide": "One row per position (columns per cutoff)"}.get)
    view = to_wide(shown) if fmt == "wide" else shown
    st.dataframe(view, hide_index=True, width="stretch")
    c = st.columns(4)
    c[0].download_button("Download CSV", csv_bytes(view), "early_window_features.csv", "text/csv")
    c[1].download_button("Download Parquet", parquet_bytes(view), "early_window_features.parquet", "application/octet-stream")
    st.caption("Level prefixes: `cell__` pools all cell observations in the window; `frame__` summarises the per-frame values (stat. across frames, linear "
               "slope per hour, late-minus-early difference between the 2nd and 1st half of the window); `traj__` uses per-track metrics recomputed from "
               "positions up to the cutoff (tracks with ≥3 observations for kinematics; divisions counted only when the parent's last row is ≤ T).")

    st.subheader("How a feature evolves with the cutoff")
    stat_cols = [c for c in table.columns if c.endswith("__mean") or c.endswith("__median") or c.endswith("__slope_per_h")]
    if stat_cols and table["cutoff_h"].nunique() > 1:
        y = st.selectbox("Feature summary", stat_cols)
        fig = go.Figure()
        for i, (sid, g) in enumerate(table.groupby("sample_id", observed=True)):
            fig.add_trace(go.Scatter(x=g["cutoff_h"], y=g[y], mode="lines+markers", name=str(sid), line=dict(color=PALETTE[i % len(PALETTE)], width=2.5)))
        style(fig, f"{y} by observation cutoff", "Observation cutoff (h)", y, 400)
        show_fig(fig, "ew_line", table[["sample_id", "cutoff_h", y]], "early_window_trend", png_button=False)
        st.caption("Windows are nested (the 24 h window contains the 12 h window), so values at different cutoffs are not independent.")

    st.subheader("Donor-level aggregation and fusion-index join (prepared, not analysed)")
    ep = ds.endpoints
    n_assigned = int((ds.samples["donor_id"] != "Unassigned").sum())
    c = st.columns(3)
    c[0].metric("Positions with donor assigned", f"{n_assigned}/{len(ds.samples)}")
    c[1].metric("Endpoint values supplied (fusion index)", len(ep))
    c[2].metric("Donors with endpoint", ep["donor_id"].nunique() if len(ep) else 0)
    if ep.empty:
        st.info("No fusion-index values have been supplied (add them to the manifest). Accordingly **no correlation with fusion index is calculated**.")
    else:
        st.success("Endpoint values are present. The joined table below is ready for later modelling; this version deliberately does not compute "
                   "fusion-index correlations or models.")
    with st.expander("Preview donor-level table and endpoint join"):
        don = aggregate_to_donor(table)
        st.dataframe(join_endpoints(don, ep, "donor"), hide_index=True, width="stretch")
        st.caption("Donor-level rows average positions within each donor. Positions without a donor appear as 'Unassigned' and must not be treated as one donor.")
