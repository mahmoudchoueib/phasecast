"""Recursive discovery, classification and pairing of morphology / tracking exports."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .readers import list_sheets

EXCLUDED_DIRS = {".venv", "venv", "env", "processed", "__pycache__", ".git", "hmx", "tests", "docs", "scripts", "exports", "node_modules", ".pytest_cache",
                 "$recycle.bin"}
MORPH_NAME_RE = re.compile(r"^(?P<key>.+?)[-_ ]+(?:MOR|MORPH|MORPHOLOGY)$", re.I)
TRACK_NAME_RE = re.compile(r"^(?P<key>.+?)[-_ ]+(?:TRACK|TRACKING|TRK)$", re.I)

MORPH_SHEETS = {"frame stats", "all cells"}
TRACK_SHEETS = {"general stats", "averages"}


@dataclass
class FoundFile:
    path: Path
    rel_path: str
    kind: str                   # morphology | tracking | unknown
    kind_by_name: str           # morphology | tracking | unknown
    kind_by_structure: str      # morphology | tracking | unknown
    key: str                    # pairing key derived from the file name (not an identifier of any donor)
    size: int
    mtime: float
    sheets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def rel_dir(self) -> str:
        """Folder of this file relative to the data directory ('' if at the top level)."""
        parent = Path(self.rel_path).parent.as_posix()
        return "" if parent == "." else parent


@dataclass
class SamplePair:
    """One imaging position: at most one morphology and one tracking file."""
    sample_id: str
    key: str
    rel_dir: str
    morphology: FoundFile | None
    tracking: FoundFile | None
    issues: list[str] = field(default_factory=list)


def classify_by_name(stem: str) -> tuple[str, str]:
    """Guess morphology/tracking from the file name suffix (-MOR / -TRACKING); also returns the pairing key (the name without the suffix)."""
    if m := MORPH_NAME_RE.match(stem):
        return "morphology", m.group("key")
    if m := TRACK_NAME_RE.match(stem):
        return "tracking", m.group("key")
    return "unknown", stem


def classify_by_structure(sheets: list[str], is_csv: bool = False) -> str:
    """Decide morphology/tracking from the worksheet names inside the workbook (more reliable than the file name)."""
    low = {s.strip().lower() for s in sheets}
    if MORPH_SHEETS <= low:
        return "morphology"
    if TRACK_SHEETS <= low:
        return "tracking"
    return "unknown"


def _iter_candidate_files(root: Path):
    """Yield every .xlsx/.csv under the data folder, skipping Excel lock files, our own CSVs and generated folders."""
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".xlsx", ".csv"}:
            continue
        if p.name.startswith("~$") or p.name.lower().startswith(("manifest", "data_dictionary")):   # lock files, our own CSVs
            continue
        rel_parts = p.relative_to(root).parts[:-1]
        if any(part.lower() in EXCLUDED_DIRS for part in rel_parts):
            continue
        yield p


def discover_files(root: Path) -> list[FoundFile]:
    """Scan the data folder, open each workbook's sheet list and return one FoundFile record per file (never reads the data itself)."""
    root = Path(root)
    found: list[FoundFile] = []
    for p in _iter_candidate_files(root):
        stat = p.stat()
        by_name, key = classify_by_name(p.stem)
        notes: list[str] = []
        try:
            sheets = list_sheets(p)
        except Exception as exc:  # unreadable / corrupt / password protected
            found.append(FoundFile(p, p.relative_to(root).as_posix(), "unknown", by_name, "unknown", key,
                                   stat.st_size, stat.st_mtime, [], [f"Could not open: {type(exc).__name__}: {exc}"]))
            continue
        by_struct = "unknown" if p.suffix.lower() == ".csv" else classify_by_structure(sheets)
        kind = by_struct if by_struct != "unknown" else by_name
        if p.suffix.lower() == ".csv":
            notes.append("CSV recognised by file name only; the table structure is checked at import.")
        elif by_struct == "unknown":
            notes.append("Worksheet structure not recognised as a HoloMonitor morphology/tracking export.")
        if by_name != "unknown" and by_struct != "unknown" and by_name != by_struct:
            notes.append(f"File name suggests {by_name} but worksheets indicate {by_struct}; worksheets were used.")
        if by_name == "unknown" and by_struct != "unknown":
            notes.append("File name has no -MOR / -TRACKING suffix; pairing by name is unreliable, needs manual resolution.")
        found.append(FoundFile(p, p.relative_to(root).as_posix(), kind, by_name, by_struct, key,
                               stat.st_size, stat.st_mtime, sheets, notes))
    return found


def _sample_id(rel_dir: str, key: str) -> str:
    """Build the unique position id: 'subfolder/key', or just 'key' when the file sits at the top level."""
    rel_dir = rel_dir.strip("/")
    return f"{rel_dir}/{key}" if rel_dir else key


def pair_files(files: list[FoundFile]) -> tuple[list[SamplePair], list[dict]]:
    """Pair morphology and tracking files that share directory and name key (case-insensitive).

    Returns (pairs, problems). Ambiguous matches (several files for one key) are NOT paired
    automatically; they are returned as problems for manual resolution.
    """
    problems: list[dict] = []
    groups: dict[tuple[str, str], dict[str, list[FoundFile]]] = {}
    for f in files:
        if f.kind == "unknown":
            problems.append(dict(file=f.rel_path, severity="warning", check="unrecognised_file",
                                 message="; ".join(f.notes) or "Unrecognised file."))
            continue
        for n in f.notes:
            problems.append(dict(file=f.rel_path, severity="warning", check="file_note", message=n))
        groups.setdefault((f.rel_dir, f.key.lower()), {"morphology": [], "tracking": []})[f.kind].append(f)

    pairs: list[SamplePair] = []
    for (rel_dir, _), by_kind in sorted(groups.items()):
        mors, trks = by_kind["morphology"], by_kind["tracking"]
        key = (mors or trks)[0].key
        sid = _sample_id(rel_dir, key)
        if len(mors) > 1 or len(trks) > 1:
            names = ", ".join(f.rel_path for f in mors + trks)
            problems.append(dict(file=names, severity="error", check="ambiguous_match", sample_id=sid,
                                 message=f"Several files share the key '{key}' in this folder; not paired automatically."))
            continue
        issues: list[str] = []
        if not mors:
            issues.append("No morphology file found for this position.")
        if not trks:
            issues.append("No tracking file found for this position.")
        for msg in issues:
            problems.append(dict(file=(mors or trks)[0].rel_path, severity="warning", check="unmatched_pair",
                                 sample_id=sid, message=msg))
        pairs.append(SamplePair(sid, key, rel_dir, mors[0] if mors else None, trks[0] if trks else None, issues))
    return pairs, problems


def discovery_table(files: list[FoundFile]) -> pd.DataFrame:
    """Turn the discovered files into a DataFrame for display."""
    return pd.DataFrame([dict(file=f.rel_path, kind=f.kind, kind_by_name=f.kind_by_name,
                              kind_by_structure=f.kind_by_structure, pairing_key=f.key, size_mb=round(f.size / 1e6, 2),
                              n_sheets=len(f.sheets), notes=" | ".join(f.notes)) for f in files])

