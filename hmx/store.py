"""Ingestion pipeline and Parquet-backed store.

Layout (all derived data; source workbooks are only ever read):
    processed/samples/<safe id>/{frames,cells,track_obs,track_summary,track_averages}.parquet
    processed/samples/<safe id>/meta.json     fingerprints, context, QC issues, column provenance
    processed/import_log.jsonl                one JSON line per import attempt (success or failure)
    processed/discovery.json                  discovery problems (unpaired / ambiguous / unrecognised files)
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import PARSER_VERSION, spec
from .discovery import FoundFile, SamplePair, discover_files, pair_files
from .endpoints import load_aliases, read_endpoint_workbook
from .mapping import (UNASSIGNED, apply_mapping, assess_samples, build_plate_table, effective_well_table, load_donor_map,
                      load_plates, load_resolutions)
from .metadata import apply_manifest, read_manifest
from .parsing import ParsedMorphology, ParsedTracking, parse_morphology, parse_tracking
from .tracks import add_family_coordinates, link_summary, link_to_morphology, track_summary
from .validation import check_morphology, check_tracking

TABLES = ["frames", "cells", "track_obs", "track_summary", "track_averages"]


def safe_id(sample_id: str) -> str:
    """Folder-safe name for a sample id (slug + short hash)."""
    slug = re.sub(r"[^0-9A-Za-z]+", "_", sample_id).strip("_")[:40] or "sample"
    return f"{slug}_{hashlib.sha1(sample_id.encode()).hexdigest()[:8]}"


def file_sha256(path: Path) -> str:
    """SHA-256 fingerprint of a file, read in chunks (used to detect changed files)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonable(o):
    """JSON encoder fallback for numpy/pandas/timedelta values."""
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if hasattr(o, "total_seconds"):
        return o.total_seconds()
    if o is pd.NA or o is pd.NaT:
        return None
    return str(o)


@dataclass
class IngestReport:
    stale_moved: list[str] = field(default_factory=list)
    endpoint_files: list[str] = field(default_factory=list)
    processed: list[str] = field(default_factory=list)
    skipped_unchanged: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    n_files: int = 0
    seconds: float = 0.0

    def summary(self) -> str:
        """One-line description of an import run."""
        return (f"{self.n_files} files discovered | {len(self.processed)} imported, {len(self.skipped_unchanged)} unchanged "
                f"(skipped), {len(self.failed)} failed | {len(self.problems)} discovery problems | {self.seconds:.1f}s")


# ------------------------------------------------------------------------------------------------
def _fingerprint(f: FoundFile | None, previous: dict | None) -> dict | None:
    """Size, mtime and hash of a file; reuses the cached hash when size and mtime are unchanged."""
    if f is None:
        return None
    fp = dict(size=f.size, mtime=f.mtime)
    if previous and previous.get("size") == f.size and previous.get("mtime") == f.mtime:
        fp["sha256"] = previous["sha256"]          # unchanged size+mtime: trust cached hash
    else:
        fp["sha256"] = file_sha256(f.path)
    return fp


def _read_meta(sdir: Path) -> dict | None:
    """Load a sample's meta.json (None if missing or unreadable)."""
    p = sdir / "meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _log(out_dir: Path, entry: dict) -> None:
    """Append one line to processed/import_log.jsonl."""
    with open(out_dir / "import_log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(ts=time.strftime("%Y-%m-%d %H:%M:%S"), **entry), default=_jsonable) + "\n")


def _write(df: pd.DataFrame, path: Path) -> None:
    """Write a DataFrame to Parquet."""
    df.to_parquet(path, index=False)


def content_signature(df: pd.DataFrame, cols: list[str]) -> str | None:
    """Fingerprint of the MEASUREMENTS in a table (not of the file bytes).

    Two exports of the same acquisition can differ byte-for-byte (different export time, file name) yet contain
    identical measurements; hashing the values catches that. Used to detect content duplicates such as a
    TRACKING file that is a renamed copy of another position's tracking.
    """
    cols = [c for c in cols if c in df.columns]
    if df.empty or not cols:
        return None
    h = pd.util.hash_pandas_object(df[cols].round(4), index=False).to_numpy()
    return hashlib.sha1(h.tobytes()).hexdigest()


def process_pair(pair: SamplePair, sdir: Path, fp_m: dict | None, fp_t: dict | None) -> dict:
    """Parse one position and write its Parquet tables + meta.json. Raises on unrecoverable errors."""
    sdir.mkdir(parents=True, exist_ok=True)
    morph: ParsedMorphology | None = parse_morphology(pair.morphology.path) if pair.morphology else None
    trk: ParsedTracking | None = parse_tracking(pair.tracking.path) if pair.tracking else None
    issues: list[dict] = []
    prov: list[dict] = []
    summary = pd.DataFrame()
    link: dict = {}
    sid = pair.sample_id

    frames = morph.frames if morph is not None else pd.DataFrame()
    cells = morph.cells if morph is not None else pd.DataFrame()
    if morph is not None:
        issues += check_morphology(morph)
        prov += morph.provenance
    obs = pd.DataFrame()
    if trk is not None and not trk.obs.empty:
        obs = link_to_morphology(add_family_coordinates(trk.obs), cells)
        link = link_summary(obs)
        summary = track_summary(obs)
        issues += check_tracking(trk, summary, len(frames) if len(frames) else None, frames if len(frames) else None, link)
        prov += trk.provenance
    elif trk is not None:
        issues += trk.issues
    issues += [dict(severity="warning", check="unmatched_pair", message=m, table="files") for m in pair.issues]

    # frames may be absent when only tracking exists: derive a time axis from the tracking Info sheet
    if frames.empty and trk is not None and not trk.source_frames.empty:
        sf = trk.source_frames.copy()
        sf["elapsed_h"] = sf["elapsed_s"] / 3600.0
        frames = sf
        issues.append(dict(severity="warning", check="frames_from_tracking", table="frames",
                           message="No morphology file: frame list reconstructed from tracking 'Source Frames' (no absolute timestamps)."))

    for t, df in (("frames", frames), ("cells", cells), ("track_obs", obs), ("track_summary", summary),
                  ("track_averages", trk.averages if trk is not None else pd.DataFrame())):
        if len(df):
            _write(df, sdir / f"{t}.parquet")
        elif (sdir / f"{t}.parquet").exists():
            (sdir / f"{t}.parquet").unlink()      # stale derived table from an earlier import

    ctx = {}
    for c in (trk.context if trk else {}, morph.context if morph else {}):
        ctx.update({k: v for k, v in c.items() if v is not None})
    if morph is not None and trk is not None and morph.context.get("group") and trk.context.get("group") \
            and morph.context["group"] != trk.context["group"]:
        issues.append(dict(severity="error", check="pair_group_mismatch", table="files",
                           message=f"Morphology group '{morph.context['group']}' differs from tracking group "
                                   f"'{trk.context['group']}': these files may not belong together."))
    meta = dict(
        sample_id=sid, key=pair.key, rel_dir=pair.rel_dir, parser_version=PARSER_VERSION,
        morphology_file=pair.morphology.rel_path if pair.morphology else None,
        tracking_file=pair.tracking.rel_path if pair.tracking else None,
        fingerprint_morphology=fp_m, fingerprint_tracking=fp_t,
        mor_group=(morph.context.get("group") if morph else None), trk_group=(trk.context.get("group") if trk else None),
        mor_experiment=(morph.context.get("experiment") if morph else None), trk_experiment=(trk.context.get("experiment") if trk else None),
        sig_mor=content_signature(cells, ["frame_no", "area_um2", "optical_volume_um3", "perimeter_um"]),
        sig_trk=content_signature(obs, ["track_id", "elapsed_s", "area_um2", "motility_um"]),
        qualifiers=" ".join(f.qualifier for f in (pair.morphology, pair.tracking) if f is not None and f.qualifier),
        experiment_name=ctx.get("experiment"), group_label=ctx.get("group"), well_id_file=ctx.get("well_id"),
        position_id_file=ctx.get("position_id"), exported_at_morphology=(morph.info.get("Exported at") if morph else None),
        exported_at_tracking=(trk.info.get("Exported at") if trk else None),
        analysis_morphology=(morph.info.get("Analysis") if morph else None),
        analysis_tracking=(trk.info.get("Analysis") if trk else None),
        n_frames=int(len(frames)), n_cell_obs=int(len(cells)),
        n_track_obs=int(len(obs)), n_tracks=int(len(summary)),
        n_families=int(summary["family"].nunique()) if len(summary) else 0,
        acquisition_start=(frames["acquired_at"].min().isoformat() if "acquired_at" in frames and len(frames) else None),
        acquisition_end=(frames["acquired_at"].max().isoformat() if "acquired_at" in frames and len(frames) else None),
        duration_h=float(frames["elapsed_h"].max()) if "elapsed_h" in frames and len(frames) else None,
        median_interval_min=(float(frames["elapsed_s"].diff().median() / 60) if "elapsed_s" in frames and len(frames) > 1 else None),
        link=link, issues=issues, provenance=prov, status="ok",
    )
    (sdir / "meta.json").write_text(json.dumps(meta, default=_jsonable, indent=1), encoding="utf-8")
    return meta


def ingest(data_dir: Path, out_dir: Path, force: bool = False, only: list[str] | None = None,
           progress: Callable[[float, str], None] | None = None) -> IngestReport:
    """Discover, pair and import. Unchanged pairs are skipped; failures are logged and the batch continues.

    ``only``: restrict to the given sample ids (process "one file pair" or all).
    """
    t0 = time.time()
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    (out_dir / "samples").mkdir(parents=True, exist_ok=True)
    rep = IngestReport()
    files = discover_files(data_dir)
    rep.n_files = len(files)
    pairs, problems = pair_files(files)
    rep.problems = problems
    (out_dir / "discovery.json").write_text(json.dumps(problems, default=_jsonable, indent=1), encoding="utf-8")
    _write_inventory(out_dir, files, pairs)
    _ingest_endpoints(out_dir, files, rep)
    todo = [p for p in pairs if only is None or p.sample_id in only]
    for i, pair in enumerate(todo):
        if progress:
            progress(i / max(len(todo), 1), f"Importing {pair.sample_id} ({i + 1}/{len(todo)})")
        sdir = out_dir / "samples" / safe_id(pair.sample_id)
        try:
            prev = _read_meta(sdir)
            fp_m = _fingerprint(pair.morphology, (prev or {}).get("fingerprint_morphology"))
            fp_t = _fingerprint(pair.tracking, (prev or {}).get("fingerprint_tracking"))
            unchanged = (prev and not force and prev.get("parser_version") == PARSER_VERSION and prev.get("status") == "ok"
                         and _same(prev.get("fingerprint_morphology"), fp_m) and _same(prev.get("fingerprint_tracking"), fp_t)
                         and prev.get("morphology_file") == (pair.morphology.rel_path if pair.morphology else None)
                         and prev.get("tracking_file") == (pair.tracking.rel_path if pair.tracking else None))
            if unchanged:
                rep.skipped_unchanged.append(pair.sample_id)
                _log(out_dir, dict(sample_id=pair.sample_id, event="skipped_unchanged"))
                continue
            meta = process_pair(pair, sdir, fp_m, fp_t)
            rep.processed.append(pair.sample_id)
            _log(out_dir, dict(sample_id=pair.sample_id, event="imported", n_frames=meta["n_frames"],
                               n_cell_obs=meta["n_cell_obs"], n_tracks=meta["n_tracks"]))
        except Exception as exc:                       # one bad pair must not stop the batch
            tb = traceback.format_exc(limit=4)
            rep.failed.append((pair.sample_id, f"{type(exc).__name__}: {exc}"))
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "meta.json").write_text(json.dumps(dict(
                sample_id=pair.sample_id, key=pair.key, status="failed", error=f"{type(exc).__name__}: {exc}", traceback=tb,
                morphology_file=pair.morphology.rel_path if pair.morphology else None,
                tracking_file=pair.tracking.rel_path if pair.tracking else None,
                parser_version=PARSER_VERSION, issues=[dict(severity="error", check="import_failed", table="files",
                                                            message=f"{type(exc).__name__}: {exc}")]), default=_jsonable, indent=1),
                encoding="utf-8")
            _log(out_dir, dict(sample_id=pair.sample_id, event="failed", error=f"{type(exc).__name__}: {exc}", traceback=tb))
    if only is None:
        _retire_stale_samples(out_dir, {p.sample_id for p in pairs}, rep)
    if progress:
        progress(1.0, "Import finished")
    rep.seconds = time.time() - t0
    write_dictionary(out_dir)
    return rep


def _write_inventory(out_dir: Path, files: list[FoundFile], pairs: list[SamplePair]) -> None:
    """Save a table of EVERY discovered file (kind, pairing key, qualifier, notes, and the position it belongs to).

    The Dataset Inventory page reads this table; it is rewritten at each scan so it always reflects the folder.
    """
    where: dict[str, str] = {}
    for p in pairs:
        for f in (p.morphology, p.tracking):
            if f is not None:
                where[f.rel_path] = p.sample_id
    inv = pd.DataFrame([dict(rel_path=f.rel_path, folder=f.rel_dir, file_name=f.path.name, kind=f.kind, kind_by_name=f.kind_by_name,
                             kind_by_structure=f.kind_by_structure, pairing_key=f.key, qualifier=f.qualifier,
                             size_mb=round(f.size / 1e6, 3), n_sheets=len(f.sheets), sample_id=where.get(f.rel_path, ""),
                             notes=" | ".join(f.notes)) for f in files])
    inv.to_parquet(out_dir / "inventory.parquet", index=False)


def _ingest_endpoints(out_dir: Path, files: list[FoundFile], rep: IngestReport) -> None:
    """Parse every endpoint workbook found (small: re-read each scan) into processed/endpoints/*.parquet + meta.json."""
    edir = out_dir / "endpoints"
    edir.mkdir(exist_ok=True)
    for old in edir.glob("*"):
        old.unlink()
    for f in files:
        if f.kind != "endpoints":
            continue
        try:
            res = read_endpoint_workbook(f.path)
            res.table.to_parquet(edir / f"{safe_id(f.rel_path)}.parquet", index=False)
            (edir / f"{safe_id(f.rel_path)}.json").write_text(json.dumps(dict(
                source=f.rel_path, columns=res.columns.to_dict("records"), issues=res.issues), default=_jsonable, indent=1), encoding="utf-8")
            rep.endpoint_files.append(f.rel_path)
        except Exception as exc:                      # never stop the batch
            rep.problems.append(dict(file=f.rel_path, severity="error", check="endpoint_import_failed", message=f"{type(exc).__name__}: {exc}"))


def _retire_stale_samples(out_dir: Path, current_ids: set[str], rep: IngestReport) -> None:
    """Move processed folders whose source files are no longer found to processed/_stale/ (never deleted)."""
    stale_root = out_dir / "_stale"
    for d in sorted((out_dir / "samples").glob("*")):
        m = _read_meta(d)
        if m and m.get("sample_id") not in current_ids:
            stale_root.mkdir(exist_ok=True)
            target = stale_root / d.name
            if target.exists():
                import shutil
                shutil.rmtree(target)
            d.rename(target)
            rep.stale_moved.append(str(m.get("sample_id")))


def _same(a: dict | None, b: dict | None) -> bool:
    """True if two fingerprints have the same hash."""
    if a is None or b is None:
        return a is None and b is None
    return a.get("sha256") == b.get("sha256")


# ------------------------------------------------------------------------------------------------
@dataclass
class Dataset:
    samples: pd.DataFrame
    frames: pd.DataFrame
    cells: pd.DataFrame
    track_obs: pd.DataFrame
    track_summary: pd.DataFrame
    track_averages: pd.DataFrame
    endpoints: pd.DataFrame
    issues: pd.DataFrame
    dictionary: pd.DataFrame
    import_log: pd.DataFrame
    manifest: pd.DataFrame
    # --- PhaseCast additions (defaults keep older constructor calls working) ---
    wells: pd.DataFrame = field(default_factory=pd.DataFrame)             # one row per (plate, well): donor + mapping status
    plates: pd.DataFrame = field(default_factory=pd.DataFrame)            # one row per plate: label + geometry (+ status)
    inventory: pd.DataFrame = field(default_factory=pd.DataFrame)         # one row per discovered file
    endpoint_columns: pd.DataFrame = field(default_factory=pd.DataFrame)  # documentation of endpoint columns
    endpoint_issues: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta_dir: Path | None = None                                          # where donor_map.csv etc. live

    @property
    def empty(self) -> bool:
        """True when no samples have been imported."""
        return self.samples.empty

    def subset(self, sample_ids: list[str]) -> "Dataset":
        """Return a copy of the dataset restricted to the given sample ids (used by the sidebar filters)."""
        ids = set(sample_ids)
        pick = lambda df: df[df["sample_id"].isin(ids)] if "sample_id" in df and len(df) else df  # noqa: E731
        return Dataset(pick(self.samples), pick(self.frames), pick(self.cells), pick(self.track_obs),
                       pick(self.track_summary), pick(self.track_averages), self.endpoints, pick(self.issues),
                       self.dictionary, self.import_log, self.manifest, self.wells, self.plates, self.inventory,
                       self.endpoint_columns, self.endpoint_issues, self.meta_dir)


def _read_parquet_safe(p: Path) -> pd.DataFrame:
    """Read a Parquet file, or an empty DataFrame if it does not exist."""
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def _load_endpoints(out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read processed endpoint tables -> (donor-level endpoint table, column documentation, issues)."""
    tabs, cols, iss = [], [], []
    for pq in sorted((out_dir / "endpoints").glob("*.parquet")):
        tabs.append(pd.read_parquet(pq))
        js = pq.with_suffix(".json")
        if js.exists():
            meta = json.loads(js.read_text(encoding="utf-8"))
            cols += [dict(source=meta["source"], **c) for c in meta.get("columns", [])]
            iss += [dict(source=meta["source"], **i) for i in meta.get("issues", [])]
    table = pd.concat(tabs, ignore_index=True) if tabs else pd.DataFrame()
    if len(table) and table["donor_id"].duplicated().any():
        iss.append(dict(source="(all files)", severity="error", check="endpoint_duplicate_donor_across_files",
                        message="The same donor appears in more than one endpoint file / row: " + ", ".join(sorted(set(table.loc[table["donor_id"].duplicated(keep=False), "donor_id"])))))
    return table, pd.DataFrame(cols), pd.DataFrame(iss)


def load_dataset(out_dir: Path, manifest_path: Path | None = None, exclude_duplicates: bool = True,
                 data_dir: Path | None = None, meta_dir: Path | None = None) -> Dataset:
    """Load all processed samples and stack them into combined tables, with donor mapping applied.

    Data flow (each step is a DataFrame operation):
      1. read every sample's meta.json  -> one row per imaging position (``samples``)
      2. legacy sample manifest         -> passage / culture date / manual per-sample donor (optional)
      3. assess_samples                 -> plate id, well/position, label-conflict and duplicate flags
      4. effective_well_table           -> donor per (plate, well) from folders + manual decisions
      5. apply_mapping                  -> donor, mapping status, analysis eligibility on every position
      6. stack each table across samples and JOIN the hierarchy columns (donor, plate, well, ...) onto it
    """
    out_dir = Path(out_dir)
    meta_dir = Path(meta_dir) if meta_dir is not None else Path(out_dir).parent / "metadata"
    metas = [m for d in sorted((out_dir / "samples").glob("*")) if (m := _read_meta(d)) is not None]
    rows, issue_rows = [], []
    for m in metas:
        sid = m["sample_id"]
        for it in m.get("issues", []):
            issue_rows.append({**{"sample_id": sid}, **it})
        if m.get("status") != "ok":
            rows.append(dict(sample_id=sid, key=m.get("key", sid), status="failed", morphology_file=m.get("morphology_file"),
                             tracking_file=m.get("tracking_file"), well_id_file=None, position_id_file=None, rel_dir=m.get("rel_dir")))
            continue
        row = {k: v for k, v in m.items() if k not in ("issues", "provenance", "link", "fingerprint_morphology",
                                                       "fingerprint_tracking")}
        row["link_pct"] = (m.get("link") or {}).get("linked_pct")
        row["hash_morphology"] = (m.get("fingerprint_morphology") or {}).get("sha256")
        row["hash_tracking"] = (m.get("fingerprint_tracking") or {}).get("sha256")
        row["dir"] = str(out_dir / "samples" / safe_id(sid))
        rows.append(row)
    samples = pd.DataFrame(rows)
    manifest, mi = read_manifest(manifest_path)
    for it in mi:
        issue_rows.append({"sample_id": "(manifest)", **it})
    endpoints, ep_cols, ep_issues = _load_endpoints(out_dir)
    inv_path = out_dir / "inventory.parquet"
    inventory = pd.read_parquet(inv_path) if inv_path.exists() else pd.DataFrame()
    if samples.empty:
        return Dataset(samples, *(pd.DataFrame() for _ in range(5)), endpoints, pd.DataFrame(issue_rows),
                       build_dictionary(metas), _read_log(out_dir), manifest, inventory=inventory,
                       endpoint_columns=ep_cols, endpoint_issues=ep_issues, meta_dir=meta_dir)
    for c in ("well_id_file", "position_id_file", "key"):
        if c not in samples:
            samples[c] = None
    samples["source_present"] = True
    if data_dir is not None:
        for i, r in samples.iterrows():
            for col in ("morphology_file", "tracking_file"):
                if isinstance(r.get(col), str) and not (Path(data_dir) / r[col]).exists():
                    samples.at[i, "source_present"] = False
    samples, ai = apply_manifest(samples, manifest)
    for it in ai:
        if it.get("check") == "missing_identifiers":      # superseded by the plate/donor mapping layer
            continue
        issue_rows.append({"sample_id": it.pop("sample_id", "(manifest)"), **it})

    # identical file content imported twice (e.g. a copied folder) must not double-count
    samples["duplicate_of"] = ""
    ok = samples[samples["status"].eq("ok")] if "status" in samples else samples
    seen: dict[tuple, str] = {}
    for _, r in ok.sort_values("sample_id").iterrows():
        k = (r.get("hash_morphology"), r.get("hash_tracking"))
        if pd.isna(k[0]) and pd.isna(k[1]):
            continue
        if k in seen:
            samples.loc[samples["sample_id"] == r["sample_id"], "duplicate_of"] = seen[k]
            issue_rows.append(dict(sample_id=r["sample_id"], severity="warning", check="duplicate_import", table="files",
                                   message=f"File content is identical to sample '{seen[k]}'. "
                                           + ("Excluded from the analysis tables." if exclude_duplicates else "Included (not excluded).")))
        else:
            seen[k] = r["sample_id"]

    # ---- plate / well / donor mapping ---------------------------------------------------------------
    samples = assess_samples(samples, load_resolutions(meta_dir))
    aliases = load_aliases(meta_dir / "donor_aliases.csv")
    wells = effective_well_table(samples[samples["status"].eq("ok")] if "status" in samples else samples, load_donor_map(meta_dir), aliases)
    ep_ids = set(endpoints["donor_id"]) if len(endpoints) else set()
    samples = apply_mapping(samples, wells, ep_ids)
    # legacy fallback: a donor typed per sample in manifest.csv is honoured only where the mapping layer knows nothing
    legacy = (samples["mapping_status"] == "unassigned") & (samples["metadata_source"] == "manifest") & ~samples["donor_id"].isin([UNASSIGNED])
    plates = build_plate_table(samples[samples["well_id"].astype(bool)], load_plates(meta_dir))
    samples["plate_label"] = samples["plate_id"].map(dict(zip(plates["plate_id"], plates["plate_label"]))).fillna(samples["plate_id"])
    samples = samples.drop(columns=[c for c in ("flags_text",) if c in samples])

    use = samples if not exclude_duplicates else samples[samples["duplicate_of"] == ""]
    use = use[use["status"].eq("ok")] if "status" in use else use
    hier_cols = ["sample_id", "donor_id", "run_id", "plate_id", "plate_label", "well_id", "position_id", "mapping_status", "analysis_eligible"]
    hier = use[hier_cols].copy()
    for c in hier_cols[1:-1]:
        hier[c] = hier[c].astype("category")

    def collect(name: str) -> pd.DataFrame:
        """Stack one table (e.g. cells) across all samples and JOIN the hierarchy columns onto every row."""
        parts = []
        for _, r in use.iterrows():
            df = _read_parquet_safe(Path(r["dir"]) / f"{name}.parquet")
            if len(df):
                df.insert(0, "sample_id", r["sample_id"])
                parts.append(df)
        if not parts:
            return pd.DataFrame()
        out = pd.concat(parts, ignore_index=True)
        out = out.merge(hier, on="sample_id", how="left")
        out["sample_id"] = out["sample_id"].astype("category")
        return out

    tables = {t: collect(t) for t in TABLES}
    return Dataset(samples, tables["frames"], tables["cells"], tables["track_obs"], tables["track_summary"],
                   tables["track_averages"], endpoints, pd.DataFrame(issue_rows),
                   build_dictionary(metas), _read_log(out_dir), manifest, wells, plates, inventory, ep_cols, ep_issues, meta_dir)


def _read_log(out_dir: Path) -> pd.DataFrame:
    """Load the import log as a DataFrame."""
    p = out_dir / "import_log.jsonl"
    if not p.exists():
        return pd.DataFrame(columns=["ts", "sample_id", "event"])
    return pd.DataFrame([json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()])


# ------------------------------------------------------------------------------------------------
STRUCTURAL = [
    ("frames/cells", "sample_id", "(derived)", None, "-", "Position identifier = relative folder + shared file-name key (e.g. 'A1P6'). It is a pairing key, NOT a donor identifier."),
    ("frames/cells", "acquired_at", "Date time", None, "Frame stats; All cells", "Frame acquisition date-time parsed day-first (dd/mm/yyyy hh:mm:ss), 1 s resolution."),
    ("frames/cells", "elapsed_h", "(derived)", "h", "-", "Hours since the first frame of the same position (derived from Date time)."),
    ("frames/cells", "elapsed_s", "(derived)", "s", "-", "Seconds since the first frame of the same position."),
    ("frames/cells/track_obs", "frame_no", "Frame no. / Source Frames 'Number'", None, "Frame stats; All cells; Info (tracking)", "Frame index within a position (1-based)."),
    ("frames/cells", "frame_id", "Frame ID", None, "Frame stats; All cells", "Software frame identifier."),
    ("cells", "cell_id", "Cell ID", None, "All cells", "Cell label WITHIN a frame. It is NOT a persistent track identifier (the same cell has different IDs in different frames)."),
    ("cells", "region", "Region(s)", None, "All cells", "Region label; empty in the sample ('No. regions: 0')."),
    ("frames", "experiment / group / description", "Experiment / Group / Description", None, "Frame stats", "Free-text labels written by the software. Not interpreted as donor/plate identifiers."),
    ("track_obs", "track_id", "Cell / Cell name", None, "Cell n; feature matrices", "Tracked cell name, e.g. 'Cell 4-1-2' (daughter of 'Cell 4-1' by naming convention, validated against End status)."),
    ("track_obs", "family", "(sheet name)", None, "Cell n", "Worksheet name = tracked cell family (root cell and all descendants)."),
    ("track_obs", "elapsed_s", "Time", "s", "Cell n; matrices; Averages", "Time since the first frame of the position (hh:mm:ss as timedelta in Excel)."),
    ("track_obs", "parent_id / generation", "(derived from names)", None, "-", "Parent track and division generation derived from the dash-separated name."),
    ("track_obs", "morph_cell_id / link_status", "(derived)", None, "-", "INFERRED link to the morphology cell: exact equality of frame, Area and Optical volume, validated by centroid displacement. Not an identifier supplied by the software."),
    ("track_summary", "(all columns)", "(derived)", None, "-", "Per-track kinematics recomputed from Relative pos X/Y; 'exported_*' columns are the last exported cumulative values."),
    ("samples", "donor_id, run_id, well_id, position_id, passage, culture_date, fusion_index", "manifest.csv", None, "-", "Provided by the user manifest; 'Unassigned' when missing. Never inferred from file names."),
]


def build_dictionary(metas: list[dict]) -> pd.DataFrame:
    """Data dictionary = explicit spec + columns actually observed in the imported files."""
    observed: dict[tuple, dict] = {}
    for m in metas:
        for p in m.get("provenance", []) or []:
            k = (p["table"], p["original"], p["internal"])
            e = observed.setdefault(k, dict(sheets=set(), n=0, unit=p.get("unit"), mapping=p.get("mapping")))
            e["sheets"].add(p["sheet"])
            e["n"] += 1
    rows = []
    desc = {(f.level, f.original): f for f in spec.ALL_FEATURES}
    table_level = {"cells": "cell", "frames": None, "track_obs": "track_obs", "track_matrix": "track_obs",
                   "track_averages": "track_obs"}
    for (table, original, internal), e in sorted(observed.items()):
        f = None
        if table == "cells":
            f = desc.get(("cell", original))
        elif table == "frames":
            f = desc.get(("frame", original)) or desc.get(("image", original))
            if f is None and original in spec.CELL_MAP:
                f = desc.get(("cell", original))
        elif table in ("track_obs", "track_matrix"):
            f = desc.get(("track_obs", original))
        elif table == "track_averages":
            base = original.replace("Avg. ", "", 1)
            f = desc.get(("track_obs", base))
        if f is None:
            description, status = "Not in the explicit mapping; kept with an auto-generated name.", spec.CONFIRM
        else:
            description, status = f.description, f.status
            if table == "frames" and internal.startswith(spec.FRAME_AVG_PREFIX):
                description = "Software-exported per-frame average of the cell feature. " + description
            if table == "track_averages":
                description = "Average over tracked cells at each time point (software 'Averages' sheet). " + description
        rows.append(dict(table=table, internal_name=internal, original_name=original,
                         unit=e["unit"] if e["unit"] else "none stated", source_sheets="; ".join(sorted(e["sheets"])),
                         description=description, interpretation_status=status, mapping=e["mapping"],
                         observed_in_samples=e["n"]))
    for tbl, name, orig, unit, sheets, d in STRUCTURAL:
        rows.append(dict(table=tbl, internal_name=name, original_name=orig, unit=unit or "none stated", source_sheets=sheets,
                         description=d, interpretation_status=spec.AS_EXPORTED if orig not in ("(derived)", "(derived from names)") else "derived by this software",
                         mapping="explicit", observed_in_samples=len(metas)))
    if not observed:    # nothing imported yet: still document the specification
        for f in spec.ALL_FEATURES:
            rows.append(dict(table=f.level, internal_name=f.internal, original_name=f.original, unit=spec.split_name_unit(f.original)[1] or "none stated",
                             source_sheets="(specification)", description=f.description, interpretation_status=f.status,
                             mapping="explicit", observed_in_samples=0))
    return pd.DataFrame(rows)


def write_dictionary(out_dir: Path, extra_dirs: list[Path] | None = None) -> None:
    """Write the data dictionary (CSV and JSON) to the processed folder and any extra folders."""
    metas = [m for d in sorted((Path(out_dir) / "samples").glob("*")) if (m := _read_meta(d)) is not None]
    d = build_dictionary(metas)
    for target in [Path(out_dir)] + list(extra_dirs or []):
        target.mkdir(parents=True, exist_ok=True)
        d.to_csv(target / "data_dictionary.csv", index=False, encoding="utf-8-sig")
        (target / "data_dictionary.json").write_text(json.dumps(d.to_dict("records"), indent=1, ensure_ascii=False),
                                                     encoding="utf-8")

