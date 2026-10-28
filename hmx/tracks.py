"""Tracking analytics: step kinematics, track summaries, division / cell-cycle logic and
morphology<->tracking linkage. All functions are pure (DataFrame in, DataFrame out) and causal:
a summary computed from observations truncated at time T only uses data up to T."""
from __future__ import annotations

import numpy as np
import pandas as pd

LINK_TOL_UM = 0.05          # max disagreement between linked centroid displacement and 'Relative pos'
KEY = ["frame_no", "area_um2", "optical_volume_um3"]


def add_steps(obs: pd.DataFrame) -> pd.DataFrame:
    """Per-observation step length (µm) and step speed (µm/h) from consecutive relative positions."""
    o = obs.sort_values(["track_id", "elapsed_s"], kind="stable").copy()
    if not {"rel_x_um", "rel_y_um"} <= set(o.columns):
        o["step_um"] = np.nan
        o["step_speed_um_h"] = np.nan
        return o
    g = o.groupby("track_id", sort=False)
    dx = g["rel_x_um"].diff().astype("float64")
    dy = g["rel_y_um"].diff().astype("float64")
    dt_h = g["elapsed_s"].diff() / 3600.0
    o["step_um"] = np.hypot(dx, dy)
    o["step_speed_um_h"] = o["step_um"] / dt_h
    return o


def trailing_speed(o: pd.DataFrame, window_s: float) -> np.ndarray:
    """Mean speed (µm/h) over a trailing window, from the exported cumulative Motility and Age.
    Before `window_s` of age has elapsed the window starts at the track's first observation."""
    o = o.reset_index(drop=True)
    out = np.full(len(o), np.nan)
    age, mot = o["age_s"].to_numpy("float64"), o["motility_um"].to_numpy("float64")
    for _, idx in o.groupby("track_id", sort=False).indices.items():
        a, m = age[idx], mot[idx]
        j = np.abs(a[None, :] - np.maximum(a - window_s, 0)[:, None]).argmin(axis=1)
        dt = a - a[j]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[idx] = np.where(dt > 0, (m - m[j]) / (dt / 3600.0), np.nan)
    return out


def infer_speed_window(obs: pd.DataFrame, candidates_h=tuple(np.arange(0.5, 6.01, 0.5))) -> tuple[float, float]:
    """Window (hours) that best reproduces the exported 'Motility speed'. Returns (window_h, max abs deviation)."""
    o = obs.sort_values(["track_id", "elapsed_s"], kind="stable").reset_index(drop=True)
    best = (np.nan, np.inf)
    exp = o["motility_speed_um_h"].to_numpy("float64")
    for w in candidates_h:
        dev = np.nanmax(np.abs(trailing_speed(o, w * 3600) - exp)) if len(o) else np.inf
        if dev < best[1]:
            best = (float(w), float(dev))
    return best


def track_summary(obs: pd.DataFrame, cutoff_s: float | None = None) -> pd.DataFrame:
    """One row per track using only observations with elapsed_s <= cutoff_s (all if None)."""
    if obs.empty:
        return pd.DataFrame()
    o = obs if cutoff_s is None else obs[obs["elapsed_s"] <= cutoff_s]
    if o.empty:
        return pd.DataFrame()
    o = add_steps(o)
    has_xy = "rel_x_um" in o.columns
    g = o.groupby("track_id", sort=False)
    first, last = g.head(1).set_index("track_id"), g.tail(1).set_index("track_id")
    s = pd.DataFrame(index=first.index)
    s["family"] = first["family"]
    s["parent_id"] = first["parent_id"]
    s["generation"] = first["generation"]
    s["born_by_division"] = first["parent_id"].notna()
    s["start_s"] = first["elapsed_s"]
    s["end_s"] = last["elapsed_s"]
    s["start_frame"] = first["frame_no"]
    s["end_frame"] = last["frame_no"]
    s["n_obs"] = g.size()
    s["duration_s"] = s["end_s"] - s["start_s"]
    gaps = (g["frame_no"].diff() > 1).groupby(o["track_id"], sort=False).sum() if "frame_no" in o else 0
    s["n_frame_gaps"] = gaps
    es = last["end_status"].astype("string") if "end_status" in last else pd.Series(pd.NA, index=last.index, dtype="string")
    s["end_status"] = es
    if has_xy:
        acc = g["step_um"].sum(min_count=1)
        s["accumulated_distance_um"] = acc
        net = np.hypot((last["rel_x_um"] - first["rel_x_um"]).astype("float64"),
                       (last["rel_y_um"] - first["rel_y_um"]).astype("float64"))
        s["net_displacement_um"] = net
        s["directness"] = np.where(acc > 0, net / acc, np.nan)
        s["mean_speed_um_h"] = np.where(s["duration_s"] > 0, acc / (s["duration_s"] / 3600.0), np.nan)
        s["max_step_speed_um_h"] = g["step_speed_um_h"].max()
        s["median_step_speed_um_h"] = g["step_speed_um_h"].median()
    for src, dst in [("motility_um", "exported_motility_final_um"), ("migration_um", "exported_migration_final_um"),
                     ("motility_speed_um_h", "exported_speed_final_um_h")]:
        if src in last:
            s[dst] = last[src].astype("float64")
    for f in ["area_um2", "irregularity", "optical_thickness_avg_um", "optical_volume_um3"]:
        if f in o:
            s[f"mean_{f}"] = g[f].mean().astype("float64")
    s = s.reset_index()
    return _add_divisions(s)


def _add_divisions(s: pd.DataFrame) -> pd.DataFrame:
    """Division / cell-cycle columns.

    A track divides when its End status is 'Divides'. Its daughters are tracks whose parent_id is
    that track (naming 'Cell 4' -> 'Cell 4-1', 'Cell 4-2'; validated by `lineage_checks`).
    Cell-cycle length (for a cell that was itself born by division) = first appearance of its
    daughters - its own first appearance. This definition reproduces the software's own
    'Avg. cell cycle length' on the sample (see validation.reconcile_general_stats).
    """
    s = s.copy()
    s["divides"] = s["end_status"].fillna("").eq("Divides")
    s["division_time_s"] = np.where(s["divides"], s["end_s"], np.nan)
    kids = s.dropna(subset=["parent_id"]).groupby("parent_id").agg(n_daughters=("track_id", "size"),
                                                                   daughters_start_s=("start_s", "min"))
    s = s.merge(kids, how="left", left_on="track_id", right_index=True)
    s["n_daughters"] = s["n_daughters"].fillna(0).astype("int16")
    s["cycle_length_s"] = np.where(s["divides"] & s["born_by_division"] & (s["n_daughters"] >= 1),
                                   s["daughters_start_s"] - s["start_s"], np.nan)
    s["cycle_complete"] = s["cycle_length_s"].notna()
    return s


def lineage_checks(summary: pd.DataFrame) -> dict:
    """Validate how division events are represented before using them."""
    ids = set(summary["track_id"])
    kids = summary.dropna(subset=["parent_id"])
    parent_missing = int((~kids["parent_id"].isin(ids)).sum())
    parent_not_divides = int((kids["parent_id"].map(summary.set_index("track_id")["divides"]) != True).sum())  # noqa: E712
    divides = summary[summary["divides"]]
    not_two = int((divides["n_daughters"] != 2).sum())
    return dict(n_born_by_division=len(kids), parent_missing=parent_missing,
                parent_not_marked_divides=parent_not_divides, divisions=len(divides),
                divisions_without_two_daughters=not_two)


# ------------------------------------------------------------------------------------------------
def link_to_morphology(obs: pd.DataFrame, cells: pd.DataFrame) -> pd.DataFrame:
    """Infer which morphology cell each tracking observation corresponds to.

    The morphology 'Cell ID' is a *per-frame* label (the same physical cell has different IDs in
    different frames) and does not equal the tracking cell name, so there is no shared identifier.
    Instead observations are linked when frame, Area and Optical volume are *exactly* equal
    (float32 values copied by the software from the same measurement) and the match is unique.
    Links are then validated per family: (centroid - lineage-continuous family position) must be the
    same image point for all linked observations of the family (tolerance LINK_TOL_UM). Rows that
    disagree are unlinked ('link_inconsistent'); families with a single linked row stay unvalidated.
    """
    o = obs.copy()
    o["morph_cell_id"] = pd.array([pd.NA] * len(o), dtype="Int32")
    o["link_status"] = "not_in_morphology"
    need = set(KEY) | {"cell_id"}
    if cells.empty or not need <= set(cells.columns) or not set(KEY) <= set(o.columns):
        o["link_status"] = "no_morphology_data"
        return o
    ck = cells[KEY + ["cell_id", "centroid_x_um", "centroid_y_um"]]
    uniq = ~ck.duplicated(KEY, keep=False)
    ck_u = ck[uniq]
    o_dup = o.duplicated(KEY, keep=False)
    m = o[KEY].reset_index().merge(ck_u, on=KEY, how="left").set_index("index")
    hit = m["cell_id"].notna() & ~o_dup
    in_cells = pd.MultiIndex.from_frame(o[KEY]).isin(pd.MultiIndex.from_frame(ck[KEY]))
    ambiguous = pd.Series(in_cells, index=o.index) & ~hit
    o.loc[hit, "morph_cell_id"] = m.loc[hit, "cell_id"].astype("Int32")
    o["link_status"] = np.where(hit, "linked_unvalidated", np.where(ambiguous, "ambiguous", "not_in_morphology"))
    o["_cx"] = m["centroid_x_um"].where(hit)
    o["_cy"] = m["centroid_y_um"].where(hit)

    o["abs_x_um"] = np.nan
    o["abs_y_um"] = np.nan
    if {"fam_x_um", "fam_y_um"} <= set(o.columns):
        # Family origin (image position of the family's first cell) = centroid - family-frame position.
        ox = (o["_cx"] - o["fam_x_um"].astype("float64")).where(hit)
        oy = (o["_cy"] - o["fam_y_um"].astype("float64")).where(hit)
        fam_ox = ox.groupby(o["family"]).transform("median")
        fam_oy = oy.groupby(o["family"]).transform("median")
        dev = np.hypot(ox - fam_ox, oy - fam_oy)
        n_fam_links = hit.groupby(o["family"]).transform("sum")
        good = hit & (dev < LINK_TOL_UM)
        o.loc[good & (n_fam_links >= 2), "link_status"] = "linked_validated"
        bad = hit & (dev >= LINK_TOL_UM)
        o.loc[bad, "link_status"] = "link_inconsistent"
        o.loc[bad, "morph_cell_id"] = pd.NA
        # absolute field-of-view coordinates for every observation of families with a validated origin
        fam_ok = (good & (n_fam_links >= 2)).groupby(o["family"]).transform("any")
        origin_x = fam_ox.groupby(o["family"]).transform("first")
        origin_y = fam_oy.groupby(o["family"]).transform("first")
        o["abs_x_um"] = (o["fam_x_um"].astype("float64") + origin_x).where(fam_ok)
        o["abs_y_um"] = (o["fam_y_um"].astype("float64") + origin_y).where(fam_ok)
        for c in ("abs_x_um", "abs_y_um"):
            o[c] = o[c].astype("float32")
    return o.drop(columns=["_cx", "_cy"])


def add_family_coordinates(obs: pd.DataFrame) -> pd.DataFrame:
    """Lineage-continuous coordinates (µm) in the family's frame.

    Verified on the sample: 'Relative pos' of a founder cell is relative to its own first position,
    while a daughter's is relative to its PARENT'S LAST position. Hence
        family position = origin(track) + relative position,
        origin(founder) = 0,  origin(daughter) = family position of the parent's last observation.
    Daughters whose parent track is missing get NaN (not guessed).
    """
    o = obs.copy()
    if not {"rel_x_um", "rel_y_um", "parent_id", "generation"} <= set(o.columns):
        return o
    o = o.sort_values(["track_id", "elapsed_s"], kind="stable").reset_index(drop=True)
    o["fam_x_um"] = np.nan
    o["fam_y_um"] = np.nan
    last: dict[str, tuple[float, float]] = {}
    for gen in sorted(o["generation"].unique()):
        sel = o["generation"] == gen
        for tid, idx in o[sel].groupby("track_id", sort=False).groups.items():
            parent = o.at[idx[0], "parent_id"]
            if gen == 0 or parent is None or (isinstance(parent, float) and np.isnan(parent)):
                ox, oy = (0.0, 0.0) if gen == 0 else (np.nan, np.nan)
            else:
                ox, oy = last.get(parent, (np.nan, np.nan))
            fx = ox + o.loc[idx, "rel_x_um"].astype("float64")
            fy = oy + o.loc[idx, "rel_y_um"].astype("float64")
            o.loc[idx, "fam_x_um"] = fx
            o.loc[idx, "fam_y_um"] = fy
            last[tid] = (float(fx.iloc[-1]), float(fy.iloc[-1]))
    o["fam_x_um"] = o["fam_x_um"].astype("float32")
    o["fam_y_um"] = o["fam_y_um"].astype("float32")
    return o


def link_summary(obs: pd.DataFrame) -> dict:
    """Count how many tracking observations were linked to morphology cells, by link status."""
    if "link_status" not in obs:
        return {}
    vc = obs["link_status"].value_counts()
    n = len(obs)
    linked = int(vc.get("linked_validated", 0) + vc.get("linked_unvalidated", 0))
    return dict(n_obs=n, linked=linked, linked_pct=100 * linked / n if n else np.nan, **{k: int(v) for k, v in vc.items()})
