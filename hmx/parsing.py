"""Parsers for HoloMonitor morphology and tracking workbooks.

The parsers locate header rows by content (not by fixed row numbers), keep the original column names
and units in a provenance list, and map to internal names only via the explicit tables in ``spec``.
Nothing is dropped silently: unmapped columns are retained (``x_<slug>``) and reported.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import spec
from .readers import Rows, find_header_row, key_value_rows, read_all_sheets, to_seconds

DT_FORMAT = "%d/%m/%Y %H:%M:%S"     # day-first; verified on the sample (dates such as 24/07/2026 occur)
GROUP_RE = re.compile(r"Well\s+(?P<well>\S+)\s*-\s*Position\s+(?P<pos>\S+)", re.I)
CELL_NAME_RE = re.compile(r"^Cell\s+(\d+(?:-\d+)*)$")


@dataclass
class ParsedMorphology:
    info: dict = field(default_factory=dict)
    frames: pd.DataFrame = field(default_factory=pd.DataFrame)
    cells: pd.DataFrame = field(default_factory=pd.DataFrame)
    provenance: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)      # experiment, group, well/position as written in the file


@dataclass
class ParsedTracking:
    info: dict = field(default_factory=dict)
    general: dict = field(default_factory=dict)
    end_status_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    source_frames: pd.DataFrame = field(default_factory=pd.DataFrame)
    averages: pd.DataFrame = field(default_factory=pd.DataFrame)
    obs: pd.DataFrame = field(default_factory=pd.DataFrame)
    matrices: dict = field(default_factory=dict)       # internal feature -> long DataFrame (track_id, elapsed_s, value)
    provenance: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------
def _issue(severity: str, check: str, message: str, **kw) -> dict:
    """Small helper that builds an issue record (severity, check name, message) used by the QC report."""
    return dict(severity=severity, check=check, message=message, **kw)


def _table(rows: Rows, markers: set[str]) -> tuple[pd.DataFrame, int | None, tuple | None]:
    """Header-detected table. Returns (df, header_row_index, band_row)."""
    h = find_header_row(rows, markers)
    if h is None:
        return pd.DataFrame(), None, None
    header = [str(c).strip() if c is not None else f"unnamed_{i}" for i, c in enumerate(rows[h][:])]
    # trim trailing unnamed columns
    while header and header[-1].startswith("unnamed_"):
        header.pop()
    body = [tuple(r[: len(header)]) + (None,) * max(0, len(header) - len(r)) for r in rows[h + 1:]]
    body = [r for r in body if any(c not in (None, "") for c in r)]
    df = pd.DataFrame(body, columns=header)
    band = rows[h - 1] if h >= 1 else None
    return df, h, band


def _parse_datetimes(series: pd.Series, issues: list[dict], where: str) -> pd.Series:
    """Parse day-first 'dd/mm/yyyy hh:mm:ss' timestamps; falls back with a warning if the format differs."""
    if series.map(lambda v: isinstance(v, (dt.datetime, pd.Timestamp))).all():
        return pd.to_datetime(series)
    s = series.astype(str).str.strip()
    out = pd.to_datetime(s, format=DT_FORMAT, errors="coerce")
    bad = out.isna()
    if bad.any():
        alt = pd.to_datetime(s[bad], dayfirst=True, errors="coerce")
        out[bad] = alt
        issues.append(_issue("warning", "timestamp_format",
                             f"{int(bad.sum())} timestamps in '{where}' did not match {DT_FORMAT}; parsed day-first by fallback.",
                             n_records=int(bad.sum())))
        if out.isna().any():
            issues.append(_issue("error", "timestamp_unparsed",
                                 f"{int(out.isna().sum())} timestamps in '{where}' could not be parsed.",
                                 n_records=int(out.isna().sum())))
    return out


def _map_columns(cols: list[str], maps: list[dict], table: str, sheet: str, prov: list[dict],
                 skip: set[str]) -> dict[str, str]:
    """Return {original: internal} for non-identity columns, recording provenance."""
    out: dict[str, str] = {}
    for c in cols:
        if c in skip:
            continue
        internal, mtype = None, "explicit"
        for m in maps:
            if c in m:
                internal = m[c]
                break
        if internal is None:
            internal, mtype = "x_" + spec.slugify(c), "auto"
        _, unit = spec.split_name_unit(c)
        out[c] = internal
        prov.append(dict(table=table, sheet=sheet, original=c, internal=internal, unit=unit, mapping=mtype))
    return out


def _to_f32(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Convert the given columns to float32 (the precision the software exports; halves memory and keeps exact equality)."""
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df


def parse_group(group: str | None) -> dict:
    """Read well and position from a label like 'Well A1 - Position 6'."""
    if isinstance(group, str) and (m := GROUP_RE.search(group)):
        return dict(well_id=m.group("well"), position_id=m.group("pos"))
    return dict(well_id=None, position_id=None)


# ------------------------------------------------------------------------------------------------
def parse_morphology(path: Path) -> ParsedMorphology:
    """Read a morphology workbook into frames (Frame stats + Frame info joined) and cells (All cells), with elapsed time and column provenance."""
    sheets = read_all_sheets(Path(path))
    res = ParsedMorphology()
    low = {k.strip().lower(): k for k in sheets}

    if "info" in low:
        res.info = key_value_rows(sheets[low["info"]])

    # ---- Frame stats ---------------------------------------------------------------------------
    fs, ci = pd.DataFrame(), pd.DataFrame()
    if "frame stats" in low:
        fs, _, _ = _table(sheets[low["frame stats"]], {"Experiment"})
    if "all cells" in low:
        ci, _, _ = _table(sheets[low["all cells"]], {"Experiment"})
    if "all cells" not in low and len(sheets) == 1:           # single-table CSV
        only = next(iter(sheets.values()))
        t, _, _ = _table(only, {"Experiment", "Frame no.", "Cell ID"})
        if "Cell ID" in t.columns:
            ci = t
        elif "No. Cells" in t.columns:
            fs = t
    if ci.empty and fs.empty:
        res.issues.append(_issue("error", "morphology_unreadable",
                                 "Neither a 'Frame stats' nor an 'All cells' table could be located."))
        return res

    id_cols_fs = {"Experiment", "Group", "Date time", "Description", "Frame ID", "Frame no."}
    id_cols_ci = {"Experiment", "Group", "Date time", "Frame ID", "Frame no.", "Cell ID", "Region(s)"}

    # ---- cells ---------------------------------------------------------------------------------
    if not ci.empty:
        cmap = _map_columns(list(ci.columns), [spec.CELL_MAP], "cells", low.get("all cells", "csv"), res.provenance, id_cols_ci)
        feats = list(cmap.values())
        cells = ci.rename(columns={"Frame no.": "frame_no", "Frame ID": "frame_id", "Cell ID": "cell_id",
                                   "Region(s)": "region", "Date time": "acquired_at", "Experiment": "experiment",
                                   "Group": "group"})
        cells = cells.rename(columns=cmap)
        cells["acquired_at"] = _parse_datetimes(cells["acquired_at"], res.issues, "All cells") if "acquired_at" in cells else pd.NaT
        cells = _to_f32(cells, feats)
        cells["frame_no"] = cells["frame_no"].astype("int32")
        cells["cell_id"] = pd.to_numeric(cells["cell_id"], errors="coerce").astype("Int32")
        cells["region"] = cells.get("region", "").astype("string").fillna("")
        res.cells = cells

    # ---- frames --------------------------------------------------------------------------------
    if not fs.empty:
        fmap = _map_columns(list(fs.columns), [spec.FRAME_MAP, spec.FRAME_AVG_MAP], "frames",
                            low.get("frame stats", "csv"), res.provenance, id_cols_fs)
        frames = fs.rename(columns={"Frame no.": "frame_no", "Frame ID": "frame_id", "Date time": "acquired_at",
                                    "Experiment": "experiment", "Group": "group", "Description": "description"})
        frames = frames.rename(columns=fmap)
        frames["acquired_at"] = _parse_datetimes(frames["acquired_at"], res.issues, "Frame stats")
        frames = _to_f32(frames, list(fmap.values()))
        frames["frame_no"] = frames["frame_no"].astype("int32")
        if "n_cells" in frames:
            frames["n_cells"] = frames["n_cells"].astype("int32")
    else:
        # derive a minimal frame table from the cell table; clearly flagged
        g = res.cells.groupby("frame_no")
        frames = g.agg(frame_id=("frame_id", "first"), acquired_at=("acquired_at", "first"),
                       experiment=("experiment", "first"), group=("group", "first"),
                       n_cells=("cell_id", "size")).reset_index()
        res.issues.append(_issue("warning", "frames_derived",
                                 "No 'Frame stats' table: frame list was derived from cell observations (frames with zero cells are invisible)."))

    # ---- frame info (image-level) -------------------------------------------------------------
    if "frame info" in low:
        fi, _, _ = _table(sheets[low["frame info"]], {"Experiment"})
        if not fi.empty:
            imap = _map_columns(list(fi.columns), [spec.IMAGE_MAP], "frames", low["frame info"], res.provenance,
                                id_cols_fs)
            fi = fi.rename(columns={"Frame no.": "frame_no", "Frame ID": "frame_id"}).rename(columns=imap)
            fi = _to_f32(fi, list(imap.values()))
            fi["frame_no"] = fi["frame_no"].astype("int32")
            keep = ["frame_no"] + list(imap.values())
            frames = frames.merge(fi[keep], on="frame_no", how="left", validate="one_to_one")

    # ---- elapsed time (relative to first frame of this position) ------------------------------
    frames = frames.sort_values("frame_no").reset_index(drop=True)
    t0 = frames["acquired_at"].min()
    frames["elapsed_s"] = (frames["acquired_at"] - t0).dt.total_seconds()
    frames["elapsed_h"] = frames["elapsed_s"] / 3600.0
    res.frames = frames
    if not res.cells.empty:
        et = frames.set_index("frame_no")[["elapsed_s", "elapsed_h"]]
        res.cells = res.cells.join(et, on="frame_no")
        if "acquired_at" not in res.cells or res.cells["acquired_at"].isna().all():
            res.cells["acquired_at"] = res.cells["frame_no"].map(frames.set_index("frame_no")["acquired_at"])

    first = frames.iloc[0]
    res.context = dict(experiment=first.get("experiment"), group=first.get("group"),
                       description=first.get("description", None), **parse_group(first.get("group")),
                       exported_at=res.info.get("Exported at"), analysis=res.info.get("Analysis"))
    return res


# ------------------------------------------------------------------------------------------------
def _parse_end_status_table(rows: Rows) -> pd.DataFrame:
    """Read the 'Cell name / End status' list at the bottom of the tracking 'General stats' sheet."""
    h = find_header_row(rows, {"Cell name"})
    if h is None:
        return pd.DataFrame(columns=["track_id", "end_status"])
    recs = [(r[0], "" if r[1] is None else str(r[1])) for r in rows[h + 1:] if r and r[0]]
    return pd.DataFrame(recs, columns=["track_id", "end_status"])


def _parse_source_frames(rows: Rows) -> pd.DataFrame:
    """Read the tracking 'Info' sheet's list of source frames (time offset -> frame number)."""
    h = find_header_row(rows, {"Date and Time"}, max_scan=len(rows))
    if h is None:
        for i, r in enumerate(rows):
            if r and isinstance(r[0], str) and r[0].strip().startswith("Date and Time"):
                h = i
                break
    if h is None:
        return pd.DataFrame(columns=["elapsed_s", "frame_no"])
    recs = []
    for r in rows[h + 1:]:
        s = to_seconds(r[0]) if r else None
        if s is None:
            continue
        recs.append((s, int(r[3]) if len(r) > 3 and r[3] is not None else None))
    return pd.DataFrame(recs, columns=["elapsed_s", "frame_no"])


def parse_tracking(path: Path) -> ParsedTracking:
    """Read a tracking workbook: Info, General stats, Averages, feature matrices and one sheet per cell family -> per-observation table with lineage columns."""
    sheets = read_all_sheets(Path(path))
    res = ParsedTracking()
    low = {k.strip().lower(): k for k in sheets}

    if "info" in low:
        rows = sheets[low["info"]]
        res.info = key_value_rows(rows)
        res.source_frames = _parse_source_frames(rows)
        if not res.source_frames.empty:
            gcol = None
            for r in rows:   # Experiment / Group of the source frames
                if len(r) >= 3 and r[1] and r[2] and isinstance(r[2], str) and GROUP_RE.search(r[2]):
                    gcol = (r[1], r[2])
                    break
            if gcol:
                res.context = dict(experiment=gcol[0], group=gcol[1], **parse_group(gcol[1]))
    if "general stats" in low:
        rows = sheets[low["general stats"]]
        res.general = key_value_rows(rows)
        res.end_status_table = _parse_end_status_table(rows)
    if "averages" in low:
        av, _, _ = _table(sheets[low["averages"]], {"Time"})
        if not av.empty:
            amap = _map_columns(list(av.columns), [spec.TRACK_AVG_MAP], "track_averages", low["averages"],
                                res.provenance, {"Time"})
            av["elapsed_s"] = av["Time"].map(to_seconds)
            av = _to_f32(av.rename(columns=amap), list(amap.values()))
            res.averages = av.drop(columns=["Time"])

    # ---- classify remaining sheets ---------------------------------------------------------------
    known = {"info", "general stats", "averages", "cell feature graphs"}
    cell_sheets, matrix_sheets, unknown = [], [], []
    for name, rows in sheets.items():
        if name.strip().lower() in known:
            continue
        h = find_header_row(rows, {"Cell name", "Cell"})
        if h is None:
            unknown.append(name)
            continue
        first = next((c for c in rows[h] if c not in (None, "")), None)
        (matrix_sheets if first == "Cell name" else cell_sheets).append(name)
    for name in unknown:
        res.issues.append(_issue("info", "unrecognised_sheet", f"Worksheet '{name}' has no recognised table and was not imported.",
                                 sheet=name))
    if "cell feature graphs" in low and len(sheets[low["cell feature graphs"]]) <= 2:
        res.issues.append(_issue("info", "graph_sheet_empty",
                                 "'Cell feature graphs' contains only a title (charts are not exported as data)."))

    # ---- feature-by-time matrices ---------------------------------------------------------------
    for name in matrix_sheets:
        rows = sheets[name]
        title = str(rows[0][0]) if rows and rows[0] and rows[0][0] else name
        internal = spec.TRACK_MAP.get(title)
        h = find_header_row(rows, {"Cell name"})
        times = [to_seconds(c) for c in rows[h][1:]]
        if internal is None:
            internal = "x_" + spec.slugify(title)
            res.provenance.append(dict(table="track_matrix", sheet=name, original=title, internal=internal,
                                       unit=spec.split_name_unit(title)[1], mapping="auto"))
        else:
            res.provenance.append(dict(table="track_matrix", sheet=name, original=title, internal=internal,
                                       unit=spec.split_name_unit(title)[1], mapping="explicit"))
        recs = []
        for r in rows[h + 1:]:
            if not r or not r[0]:
                continue
            for t, v in zip(times, r[1:]):
                if t is not None and v is not None and v != "":
                    recs.append((r[0], t, v))
        m = pd.DataFrame(recs, columns=["track_id", "elapsed_s", "value"])
        m["value"] = pd.to_numeric(m["value"], errors="coerce")
        res.matrices[internal] = m

    # ---- per-cell sheets -> observations ---------------------------------------------------------
    obs_parts = []
    for name in cell_sheets:
        rows = sheets[name]
        t, h, _ = _table(rows, {"Cell"})
        if t.empty or "Time" not in t.columns:
            continue
        tmap = _map_columns([c for c in t.columns if c not in ("Cell", "Time")], [spec.TRACK_MAP], "track_obs", "Cell n",
                            [], {"Cell", "Time"})
        track = t["Cell"].ffill()
        t = t.rename(columns=tmap)
        t.insert(0, "track_id", track.astype(str))
        t.insert(1, "family", name)
        t["elapsed_s"] = t["Time"].map(to_seconds)
        t = t.drop(columns=["Cell", "Time"])
        obs_parts.append(t)
    if cell_sheets:   # provenance recorded once
        sample_name = cell_sheets[0]
        _map_columns([c for c in _table(sheets[sample_name], {"Cell"})[0].columns if c not in ("Cell", "Time")],
                     [spec.TRACK_MAP], "track_obs", "Cell n (one sheet per tracked family)", res.provenance, {"Cell", "Time"})
    if obs_parts:
        obs = pd.concat(obs_parts, ignore_index=True)
        feats = [c for c in obs.columns if c not in ("track_id", "family", "elapsed_s", "end_status")]
        obs = _to_f32(obs, feats)
        obs["end_status"] = obs["end_status"].astype("string")
        obs = obs.sort_values(["family", "track_id", "elapsed_s"], kind="stable").reset_index(drop=True)
    elif res.matrices:
        # fallback: no per-cell sheets -> assemble from matrices (no coordinates available)
        res.issues.append(_issue("warning", "no_cell_sheets",
                                 "No per-cell tracking sheets found; observations were assembled from feature matrices. "
                                 "Coordinates (and therefore trajectories) are unavailable."))
        base = None
        for internal, m in res.matrices.items():
            m = m.rename(columns={"value": internal})
            base = m if base is None else base.merge(m, on=["track_id", "elapsed_s"], how="outer")
        base["family"] = base["track_id"].str.extract(r"^(Cell \d+)")[0]
        base["end_status"] = pd.array([None] * len(base), dtype="string")
        obs = base
    else:
        res.issues.append(_issue("error", "tracking_unreadable", "No tracking observations could be read."))
        return res

    # lineage structure from names ('Cell 4-1-2' => parent 'Cell 4-1', generation 2)
    ids = obs["track_id"]
    nm = ids.str.extract(r"^Cell\s+(?P<path>\d+(?:-\d+)*)$")["path"]
    obs["parent_id"] = nm.map(lambda p: None if p is None or (isinstance(p, float)) or "-" not in p
                              else "Cell " + p.rsplit("-", 1)[0])
    obs["generation"] = nm.map(lambda p: 0 if p is None or isinstance(p, float) else p.count("-")).astype("int16")
    if nm.isna().any():
        res.issues.append(_issue("warning", "track_name_pattern",
                                 f"{int(ids[nm.isna()].nunique())} track names do not follow 'Cell n[-k...]'; lineage not inferred for them.",
                                 n_records=int(nm.isna().sum())))

    # frame numbers via the tracking Info 'Source Frames' (exact time match, nearest otherwise)
    obs = _attach_frame_no(obs, res)
    obs = obs.reset_index(drop=True)
    res.obs = obs
    return res


def _attach_frame_no(obs: pd.DataFrame, res: ParsedTracking) -> pd.DataFrame:
    """Give each tracking observation a frame number by matching its time to the listed source-frame times."""
    sf = res.source_frames
    if sf.empty or sf["frame_no"].isna().all():
        res.issues.append(_issue("warning", "no_source_frames",
                                 "Tracking file lists no source frames; frame numbers could not be assigned."))
        obs["frame_no"] = pd.array([pd.NA] * len(obs), dtype="Int32")
        return obs
    sf = sf.sort_values("elapsed_s")
    idx = np.searchsorted(sf["elapsed_s"].to_numpy(), obs["elapsed_s"].to_numpy())
    idx = np.clip(idx, 1, len(sf) - 1)
    left, right = sf["elapsed_s"].to_numpy()[idx - 1], sf["elapsed_s"].to_numpy()[idx]
    pick = np.where(np.abs(obs["elapsed_s"].to_numpy() - left) <= np.abs(right - obs["elapsed_s"].to_numpy()), idx - 1, idx)
    gap = np.abs(sf["elapsed_s"].to_numpy()[pick] - obs["elapsed_s"].to_numpy())
    obs["frame_no"] = sf["frame_no"].to_numpy()[pick].astype("int32")
    if (gap > 1.0).any():
        res.issues.append(_issue("warning", "frame_time_mismatch",
                                 f"{int((gap > 1.0).sum())} tracking observations are >1 s from a listed source-frame time.",
                                 n_records=int((gap > 1.0).sum())))
    return obs
