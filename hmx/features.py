"""Early-window feature engineering.

For each position (sample) and observation cutoff T, features are computed ONLY from observations with
elapsed time <= T. Three feature levels are kept distinguishable in the column prefix:

    cell__<feature>__<stat>    pooled cell observations in the window (repeated measures of cells)
    frame__<feature>__<stat>   statistics across the per-frame means (one value per frame)
    traj__<metric>__<stat>     per-track metrics recomputed from observations <= T (tracks truncated at T)

Design for later work: ``aggregate_to_donor`` and ``join_endpoints`` are the hooks for donor-level
aggregation and fusion-index joins; the ingestion layer is not involved. No model fitting here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import spec
from .analysis import describe
from .tracks import track_summary

CUTOFFS_H = [6, 12, 24, 36, 48, 72]
STATS = ["mean", "median", "std", "cv", "p10", "p25", "p75", "p90", "iqr"]
META_COLS = ["sample_id", "donor_id", "run_id", "well_id", "position_id", "cutoff_h"]
TRAJ_METRICS = ["mean_speed_um_h", "net_displacement_um", "accumulated_distance_um", "directness", "duration_h", "cycle_length_h"]
MIN_TRACK_OBS = 3          # kinematic metrics need >= 2 steps


def _stats(x, prefix: str, which=STATS) -> dict:
    """Statistics (mean, median, SD, CV, percentiles, IQR, n) as a dict with prefixed column names."""
    d = describe(x)
    return {f"{prefix}__{k}": d[k] for k in which} | {f"{prefix}__n": d["n"]}


def _slope(t: np.ndarray, y: np.ndarray) -> float:
    """Least-squares slope of y against t (NaN if fewer than 3 points)."""
    ok = np.isfinite(t) & np.isfinite(y)
    if ok.sum() < 3 or np.ptp(t[ok]) == 0:
        return np.nan
    return float(np.polyfit(t[ok], y[ok], 1)[0])


def window_features_for_sample(cells: pd.DataFrame, frames: pd.DataFrame, obs: pd.DataFrame, cutoff_h: float,
                               cell_features: list[str], frame_features: list[str], traj: bool = True) -> dict:
    """All early-window features for ONE position and ONE cutoff, using only data with elapsed time <= cutoff."""
    cw = cells[cells["elapsed_h"] <= cutoff_h] if len(cells) else cells
    fw = frames[frames["elapsed_h"] <= cutoff_h] if len(frames) else frames
    ow = obs[obs["elapsed_s"] <= cutoff_h * 3600.0] if len(obs) else obs
    row: dict = dict(cutoff_h=cutoff_h, n_frames=int(len(fw)), n_cell_obs=int(len(cw)))
    used_max = max([x for x in (fw["elapsed_h"].max() if len(fw) else np.nan, cw["elapsed_h"].max() if len(cw) else np.nan,
                                (ow["elapsed_s"].max() / 3600.0) if len(ow) else np.nan) if np.isfinite(x)], default=np.nan)
    assert not (np.isfinite(used_max) and used_max > cutoff_h + 1e-9), "window leaked data beyond the cutoff"
    row["window_max_elapsed_h"] = used_max
    interval_h = frames["elapsed_h"].diff().median() if len(frames) > 1 else np.nan
    avail_max = frames["elapsed_h"].max() if len(frames) else np.nan
    row["window_complete"] = bool(np.isfinite(used_max) and np.isfinite(interval_h) and used_max >= cutoff_h - interval_h * 1.01)
    row["window_truncated_by_data_end"] = bool(np.isfinite(avail_max) and avail_max < cutoff_h - (interval_h if np.isfinite(interval_h) else 0))

    # ---- cell level (pooled cell observations) -------------------------------------------------
    for f in cell_features:
        if f in cw:
            row.update(_stats(cw[f], f"cell__{f}"))
    # ---- frame level: one value per frame (per-frame mean of cells, or a frame property) ---------
    half = cutoff_h / 2.0
    for f in frame_features:
        if f in cw and len(cw):
            ser = cw.groupby("frame_no").agg(t=("elapsed_h", "first"), v=(f, "mean"))
        elif f in fw and len(fw):
            ser = fw[["elapsed_h", f]].rename(columns={"elapsed_h": "t", f: "v"})
        else:
            continue
        ser = ser.dropna()
        row.update(_stats(ser["v"], f"frame__{f}"))
        row[f"frame__{f}__slope_per_h"] = _slope(ser["t"].to_numpy("float64"), ser["v"].to_numpy("float64"))
        e, l = ser.loc[ser["t"] <= half, "v"], ser.loc[ser["t"] > half, "v"]
        if len(e) >= 2 and len(l) >= 2:
            row[f"frame__{f}__late_minus_early"] = float(l.mean() - e.mean())
            row[f"frame__{f}__late_vs_early_pct"] = float(100 * (l.mean() - e.mean()) / e.mean()) if e.mean() else np.nan
        else:
            row[f"frame__{f}__late_minus_early"] = row[f"frame__{f}__late_vs_early_pct"] = np.nan
    # ---- trajectory level -------------------------------------------------------------------------
    if traj and len(ow):
        ts = track_summary(ow, cutoff_s=cutoff_h * 3600.0)
        ts["duration_h"] = ts["duration_s"] / 3600.0
        ts["cycle_length_h"] = ts["cycle_length_s"] / 3600.0
        row["traj__n_tracks"] = int(len(ts))
        row["traj__n_divisions"] = int(ts["divides"].sum())
        row["traj__n_complete_cycles"] = int(ts["cycle_complete"].sum())
        kin = ts[ts["n_obs"] >= MIN_TRACK_OBS]
        row["traj__n_tracks_for_kinematics"] = int(len(kin))
        for m in TRAJ_METRICS:
            src = ts if m in ("duration_h", "cycle_length_h") else kin
            if m in src:
                row.update(_stats(src[m], f"traj__{m}"))
    elif traj:
        row["traj__n_tracks"] = 0
    return row


def early_window_table(ds, cutoffs: list[float], cell_features: list[str], frame_features: list[str],
                       traj: bool = True, sample_ids: list[str] | None = None) -> pd.DataFrame:
    """One row per (position, cutoff). `ds` is a store.Dataset (already filtered or not)."""
    samples = ds.samples if sample_ids is None else ds.samples[ds.samples["sample_id"].isin(sample_ids)]
    rows = []
    cg = {k: v for k, v in ds.cells.groupby("sample_id", observed=True)} if len(ds.cells) else {}
    fg = {k: v for k, v in ds.frames.groupby("sample_id", observed=True)} if len(ds.frames) else {}
    og = {k: v for k, v in ds.track_obs.groupby("sample_id", observed=True)} if len(ds.track_obs) else {}
    empty = pd.DataFrame()
    for _, s in samples.iterrows():
        sid = s["sample_id"]
        for T in cutoffs:
            r = window_features_for_sample(cg.get(sid, empty), fg.get(sid, empty), og.get(sid, empty), float(T),
                                           cell_features, frame_features, traj)
            rows.append(dict(sample_id=sid, donor_id=s["donor_id"], run_id=s["run_id"], well_id=s["well_id"],
                             position_id=s["position_id"], **r))
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    first = [c for c in META_COLS + ["n_frames", "n_cell_obs", "window_max_elapsed_h", "window_complete", "window_truncated_by_data_end"] if c in out]
    return out[first + [c for c in out.columns if c not in first]]


def to_wide(table: pd.DataFrame) -> pd.DataFrame:
    """One row per position; feature columns suffixed with the cutoff (e.g. ``..._w24h``)."""
    ids = [c for c in ["sample_id", "donor_id", "run_id", "well_id", "position_id"] if c in table]
    feats = [c for c in table.columns if c not in ids + ["cutoff_h"]]
    parts = []
    for T, g in table.groupby("cutoff_h"):
        g = g.set_index(ids)[feats]
        g.columns = [f"{c}_w{T:g}h" for c in g.columns]
        parts.append(g)
    return pd.concat(parts, axis=1).reset_index()


def aggregate_to_donor(table: pd.DataFrame, how: str = "mean") -> pd.DataFrame:
    """Donor-level table: positions (and wells) are averaged within donor so that the donor is the unit.
    Unassigned positions are NOT merged into a donor - they stay as their own 'Unassigned' group and are flagged."""
    num = [c for c in table.select_dtypes("number").columns if c != "cutoff_h"]
    g = table.groupby(["donor_id", "cutoff_h"], observed=True)
    out = getattr(g[num], how)()
    out["n_positions"] = g["sample_id"].nunique()
    return out.reset_index()


def join_endpoints(table: pd.DataFrame, endpoints: pd.DataFrame, level: str = "donor") -> pd.DataFrame:
    """Attach supplied endpoint values (fusion index, desmin, AChE ...) by donor id.

    A *join* (merge) matches rows of two tables on a key column; here the key is ``donor_id`` and the match is
    exact. Donors without an endpoint row, and 'Unresolved'/'Unassigned' pseudo-donors, get empty (NaN) values -
    nothing is imputed or invented. With no endpoint table the table is returned with empty endpoint columns.
    """
    if endpoints is None or endpoints.empty:
        out = table.copy()
        out["fusion_index"] = np.nan
        return out
    ecols = [c for c in endpoints.columns if c.endswith(("_pct", "_u_ml")) or c == "fusion_index"]
    e = endpoints[["donor_id"] + ecols].copy()
    if "fusion_index" not in e and "fi_pct" in e:
        e["fusion_index"] = e["fi_pct"]          # convenience alias: FI (%) is the project's fusion index
    e["donor_id"] = e["donor_id"].astype(str)
    out = table.copy()
    out["donor_id"] = out["donor_id"].astype(str)
    return out.merge(e, on="donor_id", how="left")

def default_feature_sets(cells: pd.DataFrame, frames: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Sensible default cell-level and frame-level features for the early-window page."""
    cell_feats = [f for f in spec.HEADLINE_CELL_FEATURES if f in cells]
    frame_feats = [f for f in ["confluence_pct", "n_cells"] if f in frames] + [f for f in ["area_um2", "hull_convexity", "optical_thickness_avg_um"] if f in cells]
    return cell_feats, frame_feats

