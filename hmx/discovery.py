"""Recursive discovery, classification and pairing of PhaseCast input files.

Three kinds of input are recognised:

* ``morphology``  - HoloMonitor "-MOR" export (worksheets *Frame stats* + *All cells*)
* ``tracking``    - HoloMonitor "-TRACKING" export (worksheets *General stats* + *Averages*)
* ``endpoints``   - a donor-level endpoint workbook (first header cell is "Donor")

Pairing rule (deliberately conservative): a morphology file and a tracking file are paired only when
they sit in the SAME folder and share the same name key (``A1P6-MOR`` <-> ``A1P6-TRACKING``).
Anything else - a partner in another folder, two candidates for one key - is *reported*, never
silently paired.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .readers import list_sheets, peek_first_cells

EXCLUDED_DIRS = {".venv", "venv", "env", "processed", "processed_baseline", "metadata", "__pycache__", ".git", "hmx", "tests", "docs",
                 "scripts", "exports", "node_modules", ".pytest_cache", "$recycle.bin", ".streamlit"}

# '<key> <sep> <KIND> <optional qualifier>' e.g. 'A1P6-MOR', 'A2P1-MOR(BLUR', 'C2P4-TRACKING#'.
# The qualifier (anything after the kind that is not a letter/digit start) is kept as a note,
# not used for pairing, so 'A2P1-MOR(BLUR' still pairs with 'A2P1-TRACKING(BLUR'.
_NAME_RE = re.compile(r"^(?P<key>.+?)[-_ ]+(?P<kind>MOR|MORPH|MORPHOLOGY|TRACK|TRACKING|TRK)(?P<qual>[^A-Za-z0-9].*)?$", re.I)
_KIND_MAP = {"MOR": "morphology", "MORPH": "morphology", "MORPHOLOGY": "morphology",
             "TRACK": "tracking", "TRACKING": "tracking", "TRK": "tracking"}

MORPH_SHEETS = {"frame stats", "all cells"}
TRACK_SHEETS = {"general stats", "averages"}
ENDPOINT_FIRST_HEADERS = {"donor", "donor id", "donor_id"}


@dataclass
class FoundFile:
    """One file on disk plus what we could tell about it without reading its data."""
    path: Path
    rel_path: str
    kind: str                   # morphology | tracking | endpoints | unknown
    kind_by_name: str           # morphology | tracking | unknown
    kind_by_structure: str      # morphology | tracking | endpoints | unknown
    key: str                    # pairing key derived from the file name (NOT an identifier of any donor)
    size: int
    mtime: float
    sheets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    qualifier: str = ""         # trailing text after MOR/TRACKING in the name, e.g. '(BLUR'

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


def classify_by_name(stem: str) -> tuple[str, str, str]:
    """Guess the kind from the file name. Returns (kind, pairing key, qualifier)."""
    if m := _NAME_RE.match(stem):
        return _KIND_MAP[m.group("kind").upper()], m.group("key"), (m.group("qual") or "")
    return "unknown", stem, ""


def classify_by_structure(sheets: list[str]) -> str:
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
    """Scan the data folder, open each workbook's sheet list and return one FoundFile record per file.

    Only workbook *metadata* (sheet names, and the first cell of unknown workbooks) is read here, so
    scanning hundreds of files stays fast; the measurements are read later by the parsers.
    """
    root = Path(root)
    found: list[FoundFile] = []
    for p in _iter_candidate_files(root):
        stat = p.stat()
        by_name, key, qual = classify_by_name(p.stem)
        notes: list[str] = []
        rel = p.relative_to(root).as_posix()
        try:
            sheets = list_sheets(p)
        except Exception as exc:  # unreadable / corrupt / password protected
            found.append(FoundFile(p, rel, "unknown", by_name, "unknown", key, stat.st_size, stat.st_mtime, [],
                                   [f"Could not open: {type(exc).__name__}: {exc}"], qual))
            continue
        by_struct = "unknown" if p.suffix.lower() == ".csv" else classify_by_structure(sheets)
        if by_struct == "unknown" and by_name == "unknown":
            first = peek_first_cells(p, 1)
            if first and str(first[0]).strip().lower() in ENDPOINT_FIRST_HEADERS:
                by_struct = "endpoints"
        kind = by_struct if by_struct != "unknown" else by_name
        if p.suffix.lower() == ".csv":
            notes.append("CSV recognised by file name only; the table structure is checked at import.")
        elif by_struct == "unknown":
            notes.append("Worksheet structure not recognised as a HoloMonitor morphology/tracking export.")
        if by_name not in ("unknown",) and by_struct not in ("unknown", "endpoints") and by_name != by_struct:
            notes.append(f"File name suggests {by_name} but worksheets indicate {by_struct}; worksheets were used.")
        if by_name == "unknown" and by_struct in ("morphology", "tracking"):
            notes.append(f"File name has no -MOR / -TRACKING suffix (recognised as {by_struct} from its worksheets); "
                         "it is paired by name key only - please check the pairing.")
        if qual:
            notes.append(f"File name carries the qualifier '{qual}' after the kind (possibly a manual annotation such as image quality); "
                         "meaning unconfirmed.")
        found.append(FoundFile(p, rel, kind, by_name, by_struct, key, stat.st_size, stat.st_mtime, sheets, notes, qual))
    return found


def _sample_id(rel_dir: str, key: str) -> str:
    """Build the unique position id: 'subfolder/key', or just 'key' when the file sits at the top level."""
    rel_dir = rel_dir.strip("/")
    return f"{rel_dir}/{key}" if rel_dir else key


def pair_files(files: list[FoundFile]) -> tuple[list[SamplePair], list[dict]]:
    """Pair morphology and tracking files that share directory and name key (case-insensitive).

    Returns (pairs, problems). Ambiguous matches (several files for one key) are NOT paired
    automatically. A morphology file whose partner exists only in a *different* folder produces a
    ``cross_folder_candidate`` problem so the user can decide; it is never paired automatically.
    """
    problems: list[dict] = []
    groups: dict[tuple[str, str], dict[str, list[FoundFile]]] = {}
    for f in files:
        if f.kind == "endpoints":
            continue
        if f.kind == "unknown":
            problems.append(dict(file=f.rel_path, severity="warning", check="unrecognised_file",
                                 message="; ".join(f.notes) or "Unrecognised file."))
            continue
        for n in f.notes:
            problems.append(dict(file=f.rel_path, severity="info", check="file_note", message=n))
        groups.setdefault((f.rel_dir, f.key.lower()), {"morphology": [], "tracking": []})[f.kind].append(f)

    pairs: list[SamplePair] = []
    unmatched: list[tuple[str, str, str, FoundFile]] = []      # (kind, key, sid, file)
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
            issues.append("No morphology (MOR) file found for this position.")
            unmatched.append(("morphology", key.lower(), sid, trks[0]))
        if not trks:
            issues.append("No tracking file found for this position.")
            unmatched.append(("tracking", key.lower(), sid, mors[0]))
        for msg in issues:
            problems.append(dict(file=(mors or trks)[0].rel_path, severity="warning", check="unmatched_pair",
                                 sample_id=sid, message=msg))
        pairs.append(SamplePair(sid, key, rel_dir, mors[0] if mors else None, trks[0] if trks else None, issues))

    # Candidate partners in other folders (reported only)
    for kind, k, sid, f in unmatched:
        if kind != "tracking":          # report each candidate pair once (from the side that lacks its tracking file)
            continue
        other = [(osid, of) for (okind, ok, osid, of) in unmatched if ok == k and osid != sid and okind != kind]
        # 'unmatched' entries store the kind that is missing; a complementary missing kind in another folder is a candidate
        for osid, of in other:
            problems.append(dict(file=f"{f.rel_path}  <->  {of.rel_path}", severity="warning", check="cross_folder_candidate",
                                 sample_id=sid,
                                 message=f"'{sid}' lacks its {kind} file while '{osid}' lacks the complementary one with the same name key. "
                                         "They may be one position filed in two folders. NOT paired automatically - confirm before merging."))
    return pairs, problems


def discovery_table(files: list[FoundFile]) -> pd.DataFrame:
    """Turn the discovered files into a DataFrame for display."""
    return pd.DataFrame([dict(file=f.rel_path, kind=f.kind, kind_by_name=f.kind_by_name,
                              kind_by_structure=f.kind_by_structure, pairing_key=f.key, qualifier=f.qualifier,
                              size_mb=round(f.size / 1e6, 2), n_sheets=len(f.sheets), notes=" | ".join(f.notes)) for f in files])

