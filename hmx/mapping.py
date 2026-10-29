"""Plate, well and donor mapping - the layer that answers "which donor is in which well?".

Why this layer exists
---------------------
HoloMonitor files tell us *experiment name*, *well* and *position* (from the ``Group`` text, e.g.
"Well A1 - Position 6") but **not which donor was seeded in a well**. Donor identity comes from outside
the files. PhaseCast therefore keeps it in small, human-editable CSV files (folder ``metadata/``) with
an evidence trail, and never guesses:

    plates.csv              one row per plate (experiment): label, geometry, geometry status
    donor_map.csv           one row per (plate, well): donor, status, evidence, timestamp  (manual decisions)
    donor_map_history.csv   append-only log of every change (so assignments can be corrected and audited)
    donor_aliases.csv       explicit alias -> donor id (only way two different spellings are linked)
    sample_resolution.csv   per-position decisions for flagged files (exclude / which label to trust ...)

Hierarchy:  donor -> experiment/plate -> well -> imaging position -> frame -> cell observation.
A *sample* in PhaseCast is one imaging position (one MOR/TRACKING pair).

Mapping status values
---------------------
confirmed_manual   a person assigned the well in the Plate Mapper (evidence recorded)
confirmed_folder   the raw-data folder name carries exactly one donor label (e.g. ``16M``); this is
                   the user's own filing and is used as evidence, but can be overridden manually
unresolved         several candidate donors (e.g. folder ``PLATE2-25F_40M_34F_20M``) - NOT assigned
conflict           two folders claim the same well for different donors
unassigned         no donor information at all
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .endpoints import normalise_donor_id, resolve_donor

UNASSIGNED = "Unassigned"
UNRESOLVED = "Unresolved"

# Folder-name convention used by the project: '<age><M|F>' e.g. 16M, 25F. A folder may carry several.
DONOR_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,3}[MF])(?![A-Za-z0-9])", re.I)
PLATE_TOKEN_RE = re.compile(r"plate\s*[-_ ]?\s*(\d+)", re.I)
WELL_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
KEY_RE = re.compile(r"^([A-Za-z]+\d+)P(\d+)$")
GROUP_RE = re.compile(r"Well\s+(?P<well>\S+)\s*-\s*Position\s+(?P<pos>\S+)", re.I)

PLATE_FORMATS = {6: (2, 3), 12: (3, 4), 24: (4, 6), 48: (6, 8), 96: (8, 12), 384: (16, 24)}
CONFIRMED = {"confirmed_manual", "confirmed_folder"}
RESOLUTION_ACTIONS = ["exclude", "use_mor_label", "use_trk_label", "use_filename_label", "include_as_is"]

PLATES_COLS = ["plate_id", "plate_label", "experiment_name", "n_rows", "n_cols", "geometry_status", "notes"]
DONOR_MAP_COLS = ["plate_id", "well_id", "donor_id", "status", "evidence", "updated_at"]
HISTORY_COLS = ["timestamp", "plate_id", "well_id", "old_donor_id", "old_status", "new_donor_id", "new_status", "evidence"]
RESOLUTION_COLS = ["sample_id", "action", "evidence", "updated_at"]


# ------------------------------------------------------------------------------------------------
# Small parsing helpers
# ------------------------------------------------------------------------------------------------
def slug(text: str) -> str:
    """Lower-case identifier made of letters/digits/underscores."""
    return re.sub(r"[^0-9a-z]+", "_", str(text).lower()).strip("_")


def plate_id_from_experiment(experiment: str | None) -> str:
    """Stable plate id = slug of the experiment text written by HoloMonitor (identical well names on
    different experiments therefore never collide). Missing text -> 'unknown_plate'."""
    return slug(experiment) if experiment else "unknown_plate"


def parse_well(well: str | None) -> tuple[str, int] | None:
    """'B3' -> ('B', 3); None if not a well label."""
    if not well:
        return None
    m = WELL_RE.match(str(well).strip())
    return (m.group(1).upper(), int(m.group(2))) if m else None


def parse_group_label(group: str | None) -> tuple[str | None, str | None]:
    """'Well A1 - Position 6' -> ('A1', '6')."""
    if isinstance(group, str) and (m := GROUP_RE.search(group)):
        return m.group("well").upper(), m.group("pos")
    return None, None


def parse_file_key(key: str | None) -> tuple[str | None, str | None]:
    """File-name key 'A1P6' -> ('A1', '6'). The file name is a *hand-chosen label*, not the software's record."""
    if key and (m := KEY_RE.match(str(key).strip())):
        return m.group(1).upper(), m.group(2)
    return None, None


def folder_donor_labels(rel_dir: str) -> tuple[list[str], str]:
    """Donor labels found in the folder path, taking the deepest folder that contains any.

    '.../16M' -> (['16M'], '16M');  '.../25F/DUPLICATES' -> (['25F'], '25F');
    '.../PLATE2-25F_40M_34F_20M' -> (['25F','40M','34F','20M'], that folder);  no token -> ([], '').
    """
    for comp in reversed([c for c in re.split(r"[\\/]", rel_dir or "") if c]):
        toks = [normalise_donor_id(t) for t in DONOR_TOKEN_RE.findall(comp)]
        if toks:
            return list(dict.fromkeys(toks)), comp
    return [], ""


def is_duplicates_folder(rel_dir: str) -> bool:
    """True if any folder in the path is named like 'DUPLICATES' (a user filing convention that needs confirmation)."""
    return any("duplicate" in c.lower() for c in re.split(r"[\\/]", rel_dir or ""))


def suggest_plate_format(wells: list[str]) -> tuple[int, int, int]:
    """Smallest standard plate format (rows, cols, wells) that contains every observed well label."""
    parsed = [w for w in (parse_well(x) for x in wells) if w]
    if not parsed:
        return PLATE_FORMATS[24] + (24,)
    need_rows = max(ord(r[0]) - 64 for r in parsed)
    need_cols = max(c for _, c in parsed)
    for n, (r, c) in sorted(PLATE_FORMATS.items()):
        if r >= need_rows and c >= need_cols:
            return r, c, n
    return need_rows, need_cols, need_rows * need_cols


# ------------------------------------------------------------------------------------------------
# Reading / writing the editable metadata files
# ------------------------------------------------------------------------------------------------
def _read_csv(path: Path, cols: list[str]) -> pd.DataFrame:
    """Read a metadata CSV (all text); missing file -> empty table with the right columns."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_plates(meta_dir: Path) -> pd.DataFrame:
    """The plate registry (user-confirmed geometry and labels)."""
    return _read_csv(Path(meta_dir) / "plates.csv", PLATES_COLS)


def save_plate(meta_dir: Path, plate_id: str, plate_label: str, experiment_name: str, n_rows: int, n_cols: int,
               geometry_status: str, notes: str = "") -> None:
    """Insert or update one plate row (user-confirmed geometry)."""
    meta_dir = Path(meta_dir)
    meta_dir.mkdir(parents=True, exist_ok=True)
    df = load_plates(meta_dir)
    df = df[df["plate_id"] != plate_id]
    df.loc[len(df)] = [plate_id, plate_label, experiment_name, str(n_rows), str(n_cols), geometry_status, notes]
    df.to_csv(meta_dir / "plates.csv", index=False)


def load_donor_map(meta_dir: Path) -> pd.DataFrame:
    """Manual well->donor decisions (these always win over folder-derived proposals)."""
    return _read_csv(Path(meta_dir) / "donor_map.csv", DONOR_MAP_COLS)


def save_donor_assignments(meta_dir: Path, plate_id: str, wells: list[str], donor_id: str, evidence: str,
                           status: str = "confirmed_manual") -> int:
    """Assign `wells` of a plate to a donor (or mark them 'unresolved'), keeping an audit trail.

    Every change - including a correction of an earlier assignment - appends a row to
    donor_map_history.csv with old and new values and the evidence text. Returns the number of wells changed.
    """
    if status == "confirmed_manual" and not evidence.strip():
        raise ValueError("Evidence is required for a confirmed assignment (say how the donor was established).")
    meta_dir = Path(meta_dir)
    meta_dir.mkdir(parents=True, exist_ok=True)
    cur = load_donor_map(meta_dir)
    hist = _read_csv(meta_dir / "donor_map_history.csv", HISTORY_COLS)
    donor = normalise_donor_id(donor_id) if donor_id else ""
    ts, changed, new_hist = _now(), 0, []
    for w in wells:
        w = w.upper()
        m = (cur["plate_id"] == plate_id) & (cur["well_id"] == w)
        old = cur[m].iloc[0] if m.any() else None
        new_hist.append(dict(timestamp=ts, plate_id=plate_id, well_id=w, old_donor_id=(old["donor_id"] if old is not None else ""),
                             old_status=(old["status"] if old is not None else ""), new_donor_id=donor, new_status=status,
                             evidence=evidence))
        cur = cur[~m]
        cur.loc[len(cur)] = [plate_id, w, donor, status, evidence, ts]
        changed += 1
    cur.to_csv(meta_dir / "donor_map.csv", index=False)
    pd.concat([hist, pd.DataFrame(new_hist)], ignore_index=True)[HISTORY_COLS].to_csv(meta_dir / "donor_map_history.csv", index=False)
    return changed


def clear_donor_assignments(meta_dir: Path, plate_id: str, wells: list[str], evidence: str) -> int:
    """Remove manual decisions for wells (they fall back to the folder-derived proposal). Logged in the history."""
    meta_dir = Path(meta_dir)
    cur = load_donor_map(meta_dir)
    hist = _read_csv(meta_dir / "donor_map_history.csv", HISTORY_COLS)
    ts, rows = _now(), []
    for w in wells:
        m = (cur["plate_id"] == plate_id) & (cur["well_id"] == w.upper())
        if m.any():
            o = cur[m].iloc[0]
            rows.append(dict(timestamp=ts, plate_id=plate_id, well_id=w.upper(), old_donor_id=o["donor_id"], old_status=o["status"],
                             new_donor_id="", new_status="cleared", evidence=evidence))
            cur = cur[~m]
    cur.to_csv(meta_dir / "donor_map.csv", index=False)
    pd.concat([hist, pd.DataFrame(rows)], ignore_index=True)[HISTORY_COLS].to_csv(meta_dir / "donor_map_history.csv", index=False)
    return len(rows)


def load_resolutions(meta_dir: Path) -> pd.DataFrame:
    """Per-position decisions for flagged files."""
    return _read_csv(Path(meta_dir) / "sample_resolution.csv", RESOLUTION_COLS)


def save_resolution(meta_dir: Path, sample_id: str, action: str, evidence: str) -> None:
    """Record how a flagged position is to be treated (see RESOLUTION_ACTIONS). Evidence is mandatory."""
    if action not in RESOLUTION_ACTIONS:
        raise ValueError(f"action must be one of {RESOLUTION_ACTIONS}")
    if not evidence.strip():
        raise ValueError("Evidence is required.")
    meta_dir = Path(meta_dir)
    meta_dir.mkdir(parents=True, exist_ok=True)
    df = load_resolutions(meta_dir)
    df = df[df["sample_id"] != sample_id]
    df.loc[len(df)] = [sample_id, action, evidence, _now()]
    df.to_csv(meta_dir / "sample_resolution.csv", index=False)


# ------------------------------------------------------------------------------------------------
# Sample-level assessment: plate id, label agreement, duplicates, eligibility
# ------------------------------------------------------------------------------------------------
def assess_samples(samples: pd.DataFrame, resolutions: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add plate/well/position identity, label-conflict and duplicate flags to the sample table.

    Three independent sources name a position: the **file name** ('A1P6-MOR'), the **MOR file's** ``Group``
    text and the **TRACKING file's** ``Group`` text. The Group texts are written by the microscope software;
    the file name is typed by a person. When they disagree the position is flagged ``conflict`` and (by default)
    kept out of analyses until someone decides which label is right (``sample_resolution.csv``).
    """
    s = samples.copy()
    res = {r.sample_id: r for r in (resolutions if resolutions is not None else pd.DataFrame(columns=RESOLUTION_COLS)).itertuples()}
    for c in ("mor_group", "trk_group", "mor_experiment", "trk_experiment", "rel_dir", "key"):
        if c not in s:
            s[c] = None
    recs = []
    for r in s.itertuples():
        exp = r.mor_experiment if isinstance(r.mor_experiment, str) and r.mor_experiment else (r.trk_experiment if isinstance(r.trk_experiment, str) else None)
        nw, npos = parse_file_key(r.key)
        mw, mp = parse_group_label(r.mor_group)
        tw, tp = parse_group_label(r.trk_group)
        labels = {k: v for k, v in (("file name", (nw, npos)), ("MOR file", (mw, mp)), ("TRACKING file", (tw, tp))) if v[0]}
        distinct = {v for v in labels.values()}
        flags, detail = [], ""
        if len(distinct) > 1:
            detail = "; ".join(f"{k} says {v[0]} position {v[1]}" for k, v in labels.items())
            flags.append("label_conflict")
        default_label = labels.get("MOR file") or labels.get("TRACKING file") or labels.get("file name") or (None, None)
        well, pos = default_label
        label_status = "conflict" if len(distinct) > 1 else ("consistent" if distinct else "unknown")
        if isinstance(r.mor_experiment, str) and isinstance(r.trk_experiment, str) and r.mor_experiment != r.trk_experiment:
            flags.append("experiment_conflict")
            detail += ("; " if detail else "") + f"MOR experiment '{r.mor_experiment}' vs TRACKING experiment '{r.trk_experiment}'"
        if is_duplicates_folder(r.rel_dir or ""):
            flags.append("duplicates_folder")
        action = getattr(res.get(r.sample_id), "action", "")
        if action == "use_mor_label" and mw:
            well, pos, label_status = mw, mp, "resolved"
        elif action == "use_trk_label" and tw:
            well, pos, label_status = tw, tp, "resolved"
        elif action == "use_filename_label" and nw:
            well, pos, label_status = nw, npos, "resolved"
        elif action == "include_as_is" and label_status == "conflict":
            label_status = "resolved"
        recs.append(dict(sample_id=r.sample_id, plate_id=plate_id_from_experiment(exp), experiment_label=exp,
                         well_id=well or "", position_id=pos or "", file_name_well=nw or "", file_name_position=npos or "",
                         mor_well=mw or "", mor_position=mp or "", trk_well=tw or "", trk_position=tp or "",
                         label_status=label_status, label_detail=detail, resolution_action=action,
                         flags=",".join(flags)))
    out = pd.DataFrame(recs)
    s = s.drop(columns=[c for c in out.columns if c in s.columns and c != "sample_id"]).merge(out, on="sample_id", how="left")

    # Duplicate acquisition: same plate + well + position + start time claimed by >1 position (after exclusions)
    live = s[s["resolution_action"] != "exclude"]
    if "acquisition_start" in live:
        key = live["plate_id"] + "|" + live["well_id"] + "|" + live["position_id"] + "|" + live["acquisition_start"].astype(str)
        dup = key[live["acquisition_start"].notna() & live["mor_well"].astype(bool)]
        dup_ids = set(live.loc[dup[dup.duplicated(keep=False)].index, "sample_id"])
        for i in s.index[s["sample_id"].isin(dup_ids)]:
            if s.at[i, "resolution_action"] not in ("include_as_is",):
                s.at[i, "flags"] = ",".join([f for f in [s.at[i, "flags"], "duplicate_acquisition"] if f])
    s["plate_label"] = s["plate_id"]

    # Content duplicates: the same MEASUREMENTS appear under two sample ids (e.g. a TRACKING file that is a renamed
    # copy of another position's tracking). Compared after user exclusions, so excluding one copy clears the other.
    s["content_duplicate_detail"] = ""
    for col, label in (("sig_mor", "MOR"), ("sig_trk", "TRACKING")):
        if col not in s:
            continue
        live = s[(s["resolution_action"] != "exclude") & s[col].notna()]
        for _, grp in live[live.duplicated(col, keep=False)].groupby(col):
            ids = list(grp["sample_id"])
            for i in grp.index:
                others = ", ".join(x for x in ids if x != s.at[i, "sample_id"])
                s.at[i, "content_duplicate_detail"] = "; ".join(filter(None, [s.at[i, "content_duplicate_detail"], f"{label} data identical to {others}"]))
                if s.at[i, "resolution_action"] != "include_as_is":
                    s.at[i, "flags"] = ",".join(f for f in [s.at[i, "flags"], "content_duplicate"] if f and f not in str(s.at[i, "flags"]).split(","))
    # Pair agreement: how many tracking observations coincide exactly with a morphology cell (diagnostic only).
    if "link_pct" in s:
        s["pair_agreement"] = np.select([s["morphology_file"].isna() | s["tracking_file"].isna(), s["link_pct"] >= 50, s["link_pct"] > 0],
                                        ["n/a (one file missing)", "high (same segmentation)", "partial"], "none")
    return s


def default_plate_labels(samples: pd.DataFrame) -> dict[str, str]:
    """Short display label per plate: 'PLATE3' from the experiment text, else from a folder name, else the experiment text."""
    out: dict[str, str] = {}
    for pid, g in samples.groupby("plate_id"):
        exp = g["experiment_label"].dropna().iloc[0] if g["experiment_label"].notna().any() else pid
        m = PLATE_TOKEN_RE.search(exp)
        if m:
            out[pid] = f"PLATE{m.group(1)}"
            continue
        for d in g["rel_dir"].dropna().unique():
            m = PLATE_TOKEN_RE.search(d)
            if m:
                out[pid] = f"PLATE{m.group(1)}"
                break
        else:
            out[pid] = exp
    return out


def build_plate_table(samples: pd.DataFrame, plates_saved: pd.DataFrame) -> pd.DataFrame:
    """One row per plate: label, experiment, geometry (+ status). Saved user choices override suggestions.

    The *suggested* geometry is the smallest standard plate format containing every observed well; it is
    labelled 'suggested_unconfirmed' until a person confirms it in the Plate Mapper (nothing is assumed silently).
    """
    labels = default_plate_labels(samples)
    saved = plates_saved.set_index("plate_id") if len(plates_saved) else pd.DataFrame(columns=PLATES_COLS).set_index("plate_id")
    rows = []
    for pid, g in samples.groupby("plate_id"):
        r, c, n = suggest_plate_format(list(g["well_id"].dropna().unique()))
        exp = g["experiment_label"].dropna().iloc[0] if g["experiment_label"].notna().any() else ""
        if pid in saved.index:
            sv = saved.loc[pid]
            rows.append(dict(plate_id=pid, plate_label=sv["plate_label"] or labels[pid], experiment_name=exp,
                             n_rows=int(sv["n_rows"]), n_cols=int(sv["n_cols"]), geometry_status=sv["geometry_status"] or "user_confirmed",
                             notes=sv["notes"], suggested=f"{r}x{c} ({n}-well)"))
        else:
            rows.append(dict(plate_id=pid, plate_label=labels[pid], experiment_name=exp, n_rows=r, n_cols=c,
                             geometry_status="suggested_unconfirmed", notes="", suggested=f"{r}x{c} ({n}-well)"))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------
# Well -> donor table
# ------------------------------------------------------------------------------------------------
def effective_well_table(samples: pd.DataFrame, donor_map: pd.DataFrame, aliases: dict[str, str] | None = None) -> pd.DataFrame:
    """Decide the donor of every (plate, well) from folder evidence and manual decisions.

    Precedence: (1) a manual row in donor_map.csv; (2) a single donor label in the folder name;
    (3) several labels in the folder name -> ``unresolved`` with the candidates listed;
    (4) nothing -> ``unassigned``. Two folders naming different donors for one well -> ``conflict``.
    Donor ids are only compared as exact text (after trimming / upper-casing) or via the explicit alias table.
    """
    aliases = aliases or {}
    rows: dict[tuple[str, str], dict] = {}
    for r in samples.itertuples():
        if not r.well_id:
            continue
        k = (r.plate_id, r.well_id)
        labels, comp = folder_donor_labels(r.rel_dir or "")
        e = rows.setdefault(k, dict(plate_id=r.plate_id, well_id=r.well_id, single=set(), multi=set(), evidence=[], n_positions=0))
        e["n_positions"] += 1
        if len(labels) == 1:
            e["single"].add(resolve_donor(labels[0], aliases))
            e["evidence"].append(f"folder '{comp}'")
        elif len(labels) > 1:
            e["multi"].update(resolve_donor(x, aliases) for x in labels)
            e["evidence"].append(f"folder '{comp}' lists {len(labels)} donors (no well-level information)")
    man = {(r.plate_id, r.well_id): r for r in donor_map.itertuples()}
    out = []
    for k, e in rows.items():
        cand = sorted(e["multi"] | e["single"])
        ev = "; ".join(dict.fromkeys(e["evidence"]))
        if k in man:
            m = man[k]
            donor = resolve_donor(m.donor_id, aliases) if m.donor_id else UNRESOLVED
            status = m.status if m.status in ("confirmed_manual", "unresolved") else "confirmed_manual"
            if status == "unresolved":
                donor = UNRESOLVED
            ev = f"manual: {m.evidence} ({m.updated_at})"
        elif len(e["single"]) == 1:
            donor, status = next(iter(e["single"])), "confirmed_folder"
        elif len(e["single"]) > 1:
            donor, status, ev = UNRESOLVED, "conflict", f"different folders name different donors for this well: {', '.join(sorted(e['single']))}"
        elif e["multi"]:
            donor, status = UNRESOLVED, "unresolved"
        else:
            donor, status, ev = UNASSIGNED, "unassigned", "no donor label in the folder name and no manual assignment"
        out.append(dict(plate_id=k[0], well_id=k[1], donor_id=donor, mapping_status=status, evidence=ev,
                        candidates=", ".join(cand) if status in ("unresolved", "conflict") else "", n_positions=e["n_positions"]))
    return pd.DataFrame(out, columns=["plate_id", "well_id", "donor_id", "mapping_status", "evidence", "candidates", "n_positions"])


def apply_mapping(samples: pd.DataFrame, wells: pd.DataFrame, endpoint_ids: set[str] | None = None) -> pd.DataFrame:
    """Attach donor, mapping status and the *analysis eligibility* decision to every position.

    A position is analysis-eligible only if its well's donor is confirmed AND it carries no unresolved flag
    (label conflict, duplicate acquisition, DUPLICATES folder, explicit exclusion). The reason is always recorded.
    """
    s = samples.drop(columns=[c for c in ("donor_id", "mapping_status") if c in samples.columns]).merge(
        wells[["plate_id", "well_id", "donor_id", "mapping_status", "evidence", "candidates"]].rename(
            columns={"evidence": "mapping_evidence", "candidates": "donor_candidates"}), on=["plate_id", "well_id"], how="left")
    s["donor_id"] = s["donor_id"].fillna(UNASSIGNED)
    s["mapping_status"] = s["mapping_status"].fillna("unassigned")
    reasons, elig = [], []
    for r in s.itertuples():
        why = []
        if r.mapping_status not in CONFIRMED:
            why.append(f"donor mapping is {r.mapping_status}")
        flags = set(filter(None, (r.flags or "").split(",")))
        if r.resolution_action == "exclude":
            why.append("excluded by user decision")
        if "label_conflict" in flags and r.label_status == "conflict":
            why.append("file-name / MOR / TRACKING labels disagree")
        if "experiment_conflict" in flags:
            why.append("MOR and TRACKING experiment names differ")
        if "duplicate_acquisition" in flags:
            why.append("another position claims the same plate/well/position/start time")
        if "content_duplicate" in flags:
            why.append("its measurements are identical to another position's (copied/renamed file)")
        if "duplicates_folder" in flags and r.resolution_action not in ("include_as_is",):
            why.append("file is in a folder named DUPLICATES")
        if not r.well_id:
            why.append("well unknown")
        elig.append(not why)
        reasons.append("; ".join(why) if why else "eligible")
    s["analysis_eligible"] = elig
    s["eligibility_reason"] = reasons
    if endpoint_ids is not None:
        s["has_endpoint"] = s["donor_id"].isin(endpoint_ids)
    return s


def donor_summary(samples: pd.DataFrame) -> pd.DataFrame:
    """Per donor: wells, imaging positions, MOR/TRACKING files and status counts (all positions, eligible or not)."""
    if samples.empty:
        return pd.DataFrame()
    g = samples.groupby("donor_id", observed=True)
    out = pd.DataFrame({
        "wells": g.apply(lambda d: d[["plate_id", "well_id"]].drop_duplicates().shape[0], include_groups=False),
        "imaging_positions": g.size(),
        "MOR_files": g["morphology_file"].apply(lambda x: int(x.notna().sum())),
        "TRACKING_files": g["tracking_file"].apply(lambda x: int(x.notna().sum())),
        "eligible_positions": g["analysis_eligible"].sum().astype(int),
        "plates": g["plate_id"].nunique(),
    })
    out["mapping_status"] = g["mapping_status"].agg(lambda x: ", ".join(sorted(set(x))))
    return out.reset_index()
