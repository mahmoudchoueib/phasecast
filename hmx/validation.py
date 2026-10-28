"""Data validation and quality control. Nothing here removes or alters data: checks only report."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import spec
from .parsing import ParsedMorphology, ParsedTracking
from .tracks import add_steps, infer_speed_window, lineage_checks

IRREGULAR_INTERVAL_FRAC = 0.10   # |dt - median| / median above this is reported
ROBUST_Z = 8.0


def _i(sev: str, check: str, msg: str, table: str = "", n: int | None = None, **kw) -> dict:
    """Small helper that builds a QC issue record."""
    return dict(severity=sev, check=check, message=msg, table=table, n_records=n, **kw)


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    """True if two numbers agree within a relative tolerance."""
    return bool(np.isfinite(a) and np.isfinite(b) and abs(a - b) <= tol * max(1.0, abs(b)))


# ------------------------------------------------------------------------------------------------
def check_morphology(m: ParsedMorphology) -> list[dict]:
    """QC for a morphology file: count reconciliations, duplicates, frame intervals, missing/invalid values, unmapped columns."""
    out: list[dict] = list(m.issues)
    f, c = m.frames, m.cells
    if f.empty:
        return out
    info = m.info
    n_frames_info = info.get("No. frames")
    if n_frames_info is not None:
        ok = int(n_frames_info) == len(f)
        out.append(_i("info" if ok else "warning", "frame_count_reconciles",
                      f"Info reports {n_frames_info} frames; {len(f)} parsed." + ("" if ok else " MISMATCH."), "frames", len(f)))
    n_cells_info = info.get("No. cells*")
    if n_cells_info is not None and not c.empty:
        ok = int(n_cells_info) == len(c)
        out.append(_i("info" if ok else "warning", "cell_count_reconciles",
                      f"Info reports {n_cells_info} cells (edge cells excluded by the software); {len(c)} cell rows parsed."
                      + ("" if ok else " MISMATCH."), "cells", len(c)))
    if "n_cells" in f and not c.empty:
        per_frame = c.groupby("frame_no").size().reindex(f["frame_no"]).fillna(0).astype(int).to_numpy()
        bad = int((per_frame != f["n_cells"].to_numpy()).sum())
        out.append(_i("info" if bad == 0 else "error", "frame_cells_reconcile",
                      f"Per-frame 'No. Cells' equals the number of cell rows in {len(f) - bad}/{len(f)} frames.", "frames", bad))
        zero = int((f["n_cells"] == 0).sum())
        if zero:
            out.append(_i("warning", "empty_frames", f"{zero} frames contain no cells.", "frames", zero))
    if "exp_avg_area_um2" in f and not c.empty:
        mean_a = c.groupby("frame_no")["area_um2"].mean().reindex(f["frame_no"]).to_numpy()
        rel = np.nanmax(np.abs(mean_a - f["exp_avg_area_um2"].to_numpy()) / np.abs(f["exp_avg_area_um2"].to_numpy()))
        out.append(_i("info" if rel < 1e-4 else "warning", "frame_average_reconciles",
                      f"Software 'Avg. cell data' Area equals the mean of cell areas per frame (max relative difference {rel:.2e}).",
                      "frames"))

    # duplicates
    dup_frames = int(f["frame_no"].duplicated().sum())
    if dup_frames:
        out.append(_i("error", "duplicate_frames", f"{dup_frames} duplicated frame numbers.", "frames", dup_frames))
    dup_ts = int(f["acquired_at"].duplicated().sum())
    if dup_ts:
        out.append(_i("warning", "duplicate_timestamps", f"{dup_ts} repeated acquisition timestamps.", "frames", dup_ts))
    if "frame_id" in f and f["frame_id"].duplicated().any():
        out.append(_i("warning", "duplicate_frame_id", f"{int(f['frame_id'].duplicated().sum())} repeated Frame IDs.", "frames"))
    if not c.empty:
        dup_c = int(c.duplicated(["frame_no", "cell_id"]).sum())
        out.append(_i("info" if dup_c == 0 else "error", "duplicate_cell_rows",
                      f"{dup_c} duplicated (frame, cell ID) observations.", "cells", dup_c))

    # intervals
    d = f["elapsed_s"].diff().dropna()
    if len(d):
        med = d.median()
        irregular = int(((d - med).abs() / med > IRREGULAR_INTERVAL_FRAC).sum()) if med > 0 else len(d)
        out.append(_i("info" if irregular == 0 else "warning", "frame_interval",
                      f"Median frame interval {med / 60:.2f} min (range {d.min() / 60:.2f}-{d.max() / 60:.2f} min); "
                      f"{irregular} intervals deviate >{IRREGULAR_INTERVAL_FRAC:.0%} from the median.", "frames", irregular))
        if (d <= 0).any():
            out.append(_i("error", "non_increasing_time", f"{int((d <= 0).sum())} frames are not later than their predecessor.",
                          "frames", int((d <= 0).sum())))
    out.append(_i("info", "timestamp_resolution",
                  "Morphology timestamps have 1-second resolution; tracking times have millisecond resolution.", "frames"))

    # missing / non-finite
    for name, df in (("frames", f), ("cells", c)):
        if df.empty:
            continue
        num = df.select_dtypes("number")
        miss = num.isna().sum()
        miss = miss[miss > 0]
        if len(miss):
            out.append(_i("warning", "missing_values", f"{name}: {len(miss)} numeric columns contain missing values "
                          f"(e.g. {', '.join(miss.index[:4])}).", name, int(miss.sum())))
        arr = num.to_numpy(dtype="float64", na_value=np.nan)
        n_inf = int(np.isinf(arr).sum())
        if n_inf:
            out.append(_i("error", "non_finite_values", f"{name}: {n_inf} infinite values.", name, n_inf))

    # expected ranges (flag only)
    for name, df in (("cells", c), ("frames", f)):
        if df.empty:
            continue
        for feat, (lo, hi, why) in spec.EXPECTED_RANGES.items():
            if feat in df:
                v = df[feat].astype("float64")
                n = (int((v < lo).sum()) if lo is not None else 0) + (int((v > hi).sum()) if hi is not None else 0)
                if n:
                    out.append(_i("warning", "value_out_of_expected_range", f"{feat}: {n} values outside the expected range ({why}).",
                                  name, n, feature=feat))
    nexp = df_unique(m.frames, "experiment"), df_unique(m.frames, "group")
    if nexp[0] > 1 or nexp[1] > 1:
        out.append(_i("warning", "multiple_groups_in_file",
                      f"File contains {nexp[0]} experiment names and {nexp[1]} group labels; expected one position per file.",
                      "frames"))
    unm = [p for p in m.provenance if p["mapping"] == "auto"]
    if unm:
        out.append(_i("warning", "unmapped_columns",
                      f"{len(unm)} columns are not in the explicit feature mapping and were kept with auto names: "
                      + ", ".join(p["original"] for p in unm[:6]), "dictionary", len(unm)))
    return out


def df_unique(df: pd.DataFrame, col: str) -> int:
    """Number of distinct values in a column (0 if the column is absent)."""
    return int(df[col].nunique()) if col in df else 0


# ------------------------------------------------------------------------------------------------
def track_consistency(obs: pd.DataFrame) -> list[dict]:
    """Check what the exported cumulative columns mean, using positions and times in the same file."""
    out: list[dict] = []
    needed = {"rel_x_um", "rel_y_um", "motility_um", "migration_um", "migration_directness", "motility_speed_um_h", "age_s"}
    if not needed <= set(obs.columns):
        return [_i("warning", "track_consistency_skipped", "Tracking observations lack columns needed to validate "
                   "Migration / Motility / speed definitions.", "track_obs")]
    o = add_steps(obs)
    g = o.groupby("track_id", sort=False)
    relx, rely = o["rel_x_um"].astype("float64"), o["rel_y_um"].astype("float64")
    mig = np.hypot(relx, rely)                       # distance from the track's origin (see tracks.add_family_coordinates)
    jump0 = mig.groupby(o["track_id"], sort=False).transform("first")   # origin -> first observation (0 for founder cells)
    cum = jump0 + o["step_um"].fillna(0).groupby(o["track_id"], sort=False).cumsum()
    mot = o["motility_um"].astype("float64")
    mot_first = mot.groupby(o["track_id"], sort=False).transform("first")
    checks = {
        "Migration = distance of 'Relative pos' from the track origin (founder: own first position; daughter: parent's last position)":
            (mig - o["migration_um"]).abs().max(),
        "Motility = distance origin->first position + cumulative sum of step lengths": (cum - mot).abs().max(),
        "Directness = Migration / Motility": ((o["migration_um"] / o["motility_um"]).where(o["motility_um"] > 0)
                                              - o["migration_directness"]).abs().max(),
        "Age = time since the track's first observation": (o["age_s"] - (o["elapsed_s"] - g["elapsed_s"].transform("first"))).abs().max(),
    }
    w_h, w_dev = infer_speed_window(obs)
    checks[f"Motility speed = mean speed over a trailing window of {w_h:g} h (window inferred from the data; "
           f"cumulative mean while Age < window)"] = w_dev
    for label, err in checks.items():
        tol = 0.5 if label.startswith("Motility speed") else 0.01      # speed in µm/h: timing jitter of a few 0.1 s
        ok = bool(np.isfinite(err) and err < tol)
        out.append(_i("info" if ok else "warning", "track_definition_check",
                      f"{label}: max absolute deviation {err:.3g} -> {'consistent' if ok else 'NOT consistent'}.", "track_obs"))
    return out


def reconcile_general_stats(t: ParsedTracking, summary: pd.DataFrame, n_morph_frames: int | None) -> list[dict]:
    """Compare the software's 'General stats' totals with what we recompute from the tracks."""
    out: list[dict] = []
    gs, info = t.general, t.info
    n_fam = summary["family"].nunique()
    pairs = [("No. tracked cell families", n_fam), ("No. tracked cells", len(summary)),
             ("No. cell divisions", int(summary["divides"].sum())),
             ("No. migrations out of frame", int((summary["end_status"] == "Migrates out of frame").sum())),
             ("No. disappearances", int((summary["end_status"] == "Disappears").sum())),
             ("No. stays until end", int((summary["end_status"] == "Stays until end").sum())),
             ("No. complete cell cycles", int(summary["cycle_complete"].sum()))]
    for key, mine in pairs:
        src = gs.get(key, info.get(key))
        if src is None:
            continue
        ok = int(float(src)) == int(mine)
        out.append(_i("info" if ok else "warning", "general_stats_reconcile",
                      f"{key}: export {src} vs recomputed {mine}" + ("" if ok else " - MISMATCH."), "track_summary"))
    avg = gs.get("Avg. cell cycle length")
    cyc = summary["cycle_length_s"].dropna()
    if avg is not None and len(cyc):
        exp_s = avg.total_seconds() if hasattr(avg, "total_seconds") else float(avg)
        ok = abs(exp_s - cyc.mean()) < 5
        out.append(_i("info" if ok else "warning", "general_stats_reconcile",
                      f"Avg. cell cycle length: export {exp_s / 3600:.3f} h vs recomputed {cyc.mean() / 3600:.3f} h"
                      + ("" if ok else " - MISMATCH."), "track_summary"))
    nf = gs.get("No. frames", info.get("No. frames"))
    if nf is not None:
        n_src = len(t.source_frames)
        out.append(_i("info" if int(nf) == n_src else "warning", "tracking_frames",
                      f"Tracking reports {nf} frames; {n_src} source frames listed.", "frames"))
        if n_morph_frames is not None and int(nf) != n_morph_frames:
            out.append(_i("warning", "frame_count_morph_vs_tracking",
                          f"Morphology has {n_morph_frames} frames but tracking reports {nf}.", "frames"))
    return out


def check_tracking(t: ParsedTracking, summary: pd.DataFrame, n_morph_frames: int | None,
                   morph_frames: pd.DataFrame | None, link: dict | None) -> list[dict]:
    """QC for a tracking file: definitions, lineage, continuity, matrix-vs-sheet agreement, link rate and time-axis agreement."""
    out: list[dict] = list(t.issues)
    obs = t.obs
    if obs.empty:
        return out
    out += track_consistency(obs)
    out += reconcile_general_stats(t, summary, n_morph_frames)

    lc = lineage_checks(summary)
    out.append(_i("info" if lc["parent_missing"] == 0 and lc["parent_not_marked_divides"] == 0 else "warning", "lineage",
                  f"{lc['n_born_by_division']} tracks are named as daughters; missing parents {lc['parent_missing']}, "
                  f"parents not marked 'Divides' {lc['parent_not_marked_divides']}; "
                  f"{lc['divisions_without_two_daughters']} dividing tracks without exactly two daughters.", "track_summary"))

    gaps = int(summary["n_frame_gaps"].sum())
    out.append(_i("info" if gaps == 0 else "warning", "track_continuity",
                  f"{gaps} gaps (skipped frames) inside tracks.", "track_obs", gaps))
    late_roots = summary[(~summary["born_by_division"]) & (summary["start_frame"] > 1)]
    out.append(_i("info", "tracks_entering_late",
                  f"{len(late_roots)} non-daughter tracks start after frame 1 (new cells entering the field or appearing).",
                  "track_summary", len(late_roots)))
    unclassified = summary[summary["end_status"].fillna("") == ""]
    if len(unclassified):
        out.append(_i("warning", "unclassified_end_status",
                      f"{len(unclassified)} tracks have an empty End status (e.g. {', '.join(unclassified['track_id'].head(3))}); "
                      "their fate is not recorded by the export. They are retained.", "track_summary", len(unclassified)))
    if "area_um2" in obs:
        bad = obs[obs["area_um2"] <= 0]
        if len(bad):
            out.append(_i("warning", "invalid_track_values", f"{len(bad)} tracked observations with non-positive area.", "track_obs", len(bad)))
    dup = int(obs.duplicated(["track_id", "elapsed_s"]).sum())
    out.append(_i("info" if dup == 0 else "error", "duplicate_track_rows", f"{dup} duplicated (track, time) rows.", "track_obs", dup))
    miss = obs.select_dtypes("number").isna().sum()
    miss = miss[miss > 0]
    if len(miss):
        out.append(_i("warning", "missing_values", f"track_obs: missing values in {', '.join(miss.index)}.", "track_obs", int(miss.sum())))

    # matrices vs per-cell sheets
    for feat, mat in t.matrices.items():
        if feat in obs:
            m = mat.merge(obs[["track_id", "elapsed_s", feat]], on=["track_id", "elapsed_s"], how="outer", indicator=True)
            both = m[m["_merge"] == "both"]
            only = int((m["_merge"] != "both").sum())
            dev = float((both["value"] - both[feat].astype("float64")).abs().max()) if len(both) else np.nan
            ok = only == 0 and (np.isnan(dev) or dev < 1e-3 * max(1.0, float(both["value"].abs().max())))
            out.append(_i("info" if ok else "warning", "matrix_vs_cell_sheets",
                          f"Matrix sheet '{spec.label_of(feat)}' vs per-cell sheets: {len(both)} shared values, "
                          f"{only} unmatched entries, max deviation {dev:.2g}.", "track_obs", only))
    if "avg_area_um2" in t.averages and "area_um2" in obs:
        a = obs.groupby("elapsed_s")["area_um2"].mean().astype("float64")
        av = t.averages.set_index("elapsed_s")["avg_area_um2"].astype("float64")
        j = pd.concat([a, av], axis=1, keys=["mine", "exp"]).dropna()
        if len(j):
            rel = float(((j["mine"] - j["exp"]).abs() / j["exp"].abs()).max())
            out.append(_i("info" if rel < 1e-4 else "warning", "averages_reconcile",
                          f"'Averages' sheet Avg. Area equals the mean over tracked cells (max rel. difference {rel:.2e}).", "track_obs"))
    if link:
        out.append(_i("info", "morphology_tracking_link",
                      f"{link.get('linked', 0)}/{link.get('n_obs', 0)} tracking observations ({link.get('linked_pct', float('nan')):.1f}%) "
                      f"were linked to a morphology cell by exact (frame, Area, Optical volume) equality; "
                      f"{link.get('linked_validated', 0)} are additionally validated by centroid displacement. "
                      "Unlinked cells are presumably edge cells (the morphology export excludes them) - not confirmed.",
                      "track_obs", link.get("linked", 0)))
    if morph_frames is not None and not morph_frames.empty and not t.source_frames.empty:
        mf = morph_frames.set_index("frame_no")["elapsed_s"]
        sf = t.source_frames.set_index("frame_no")["elapsed_s"]
        j = pd.concat([mf, sf], axis=1, keys=["m", "t"]).dropna()
        if len(j):
            dmax = float((j["m"] - j["t"]).abs().max())
            out.append(_i("info" if dmax <= 1.5 else "warning", "time_axis_agreement",
                          f"Morphology and tracking frame times agree to within {dmax:.2f} s over {len(j)} shared frames.", "frames"))
        else:
            out.append(_i("warning", "time_axis_agreement", "No shared frame numbers between morphology and tracking.", "frames"))
    return out


# ------------------------------------------------------------------------------------------------
def flag_records(cells: pd.DataFrame, features: list[str] | None = None) -> pd.DataFrame:
    """Record-level flags for inspection. Flags only; no record is ever removed."""
    if cells.empty:
        return pd.DataFrame()
    feats = [f for f in (features or [x.internal for x in spec.CELL_FEATURES]) if f in cells]
    keep = [c for c in ["sample_id", "frame_no", "cell_id", "elapsed_h"] if c in cells]
    parts = []
    for f in feats:
        v = cells[f].astype("float64")
        reasons = pd.Series("", index=cells.index, dtype=object)
        nonfin = ~np.isfinite(v)
        reasons[nonfin] = "missing/non-finite"
        if f in spec.EXPECTED_RANGES:
            lo, hi, why = spec.EXPECTED_RANGES[f]
            r = pd.Series(False, index=cells.index)
            if lo is not None:
                r |= v < lo
            if hi is not None:
                r |= v > hi
            reasons[r] = "outside expected range: " + why
        grp = cells["sample_id"] if "sample_id" in cells else pd.Series(0, index=cells.index)
        med = v.groupby(grp).transform("median")
        mad = (v - med).abs().groupby(grp).transform("median") * 1.4826
        z = (v - med) / mad.replace(0, np.nan)
        out_r = z.abs() > ROBUST_Z
        reasons[out_r & (reasons == "")] = f"robust outlier (|z|>{ROBUST_Z:g}, MAD-based, within position)"
        sel = reasons != ""
        if sel.any():
            p = cells.loc[sel, keep].copy()
            p["feature"], p["value"], p["reason"] = f, v[sel].to_numpy(), reasons[sel].to_numpy()
            parts.append(p)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=keep + ["feature", "value", "reason"])

