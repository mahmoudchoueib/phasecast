"""Biological endpoint workbook (donor-level measurements such as fusion index).

Structure verified on `Combined Data Jonny Project.xlsx`: one sheet, header in row 1, ONE ROW PER DONOR,
columns Donor | FI (%) | Medium Mean (U/mL) | Lysate Mean (U/mL) | Desmin % | (unnamed note column).

Rules followed here (scientific safety):
* Original header text is kept (``original_name``); internal names come only from the explicit table below.
* Donor identifiers are matched by EXACT text after trimming/upper-casing. Different spellings are only
  linked through an explicit alias table (``metadata/donor_aliases.csv``) - never by similarity.
* Values are never altered. Suspicious values (negative enzyme activity, a "?" note) are flagged, not fixed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .readers import read_all_sheets
from .spec import AS_EXPORTED, CONFIRM, split_name_unit

# original header -> (internal name, meaning, interpretation status)
ENDPOINT_MAP: dict[str, tuple[str, str, str]] = {
    "FI (%)": ("fi_pct", "Fusion index in percent (fraction of nuclei in myotubes; exact counting rule not stated in the workbook).", AS_EXPORTED),
    "Medium Mean (U/mL)": ("ache_medium_u_ml", "Acetylcholinesterase activity, mean, in the column headed 'Medium' (U/mL). "
                           "The project brief calls this 'AChE membrane'; the header says 'Medium' - confirm which is correct.", CONFIRM),
    "Lysate Mean (U/mL)": ("ache_lysate_u_ml", "Acetylcholinesterase activity, mean, in cell lysate (U/mL).", AS_EXPORTED),
    "Desmin %": ("desmin_pct", "Desmin-positive cells in percent (unit taken from the header text '%'; assay not described).", AS_EXPORTED),
}
DONOR_HEADERS = {"donor", "donor id", "donor_id"}


@dataclass
class EndpointResult:
    """Structured endpoint table plus its column documentation and any parsing issues."""
    table: pd.DataFrame = field(default_factory=pd.DataFrame)          # one row per donor
    columns: pd.DataFrame = field(default_factory=pd.DataFrame)        # data dictionary of the endpoint columns
    issues: list[dict] = field(default_factory=list)
    source: str = ""


def normalise_donor_id(text) -> str:
    """Canonical text of a donor label: trimmed, upper-case ('16m ' -> '16M'). No fuzzy matching."""
    return re.sub(r"\s+", "", str(text)).upper()


def read_endpoint_workbook(path: Path) -> EndpointResult:
    """Parse an endpoint workbook into a one-row-per-donor table. Never raises for content problems (they become issues)."""
    path = Path(path)
    res = EndpointResult(source=path.name)
    sheets = read_all_sheets(path)
    name = next(iter(sheets))
    rows = [r for r in sheets[name] if any(c not in (None, "") for c in r)]
    if not rows or str(rows[0][0]).strip().lower() not in DONOR_HEADERS:
        res.issues.append(dict(severity="error", check="endpoint_header", message=f"{path.name}: first header cell is not 'Donor'."))
        return res
    header = [str(c).strip() if c not in (None, "") else f"unnamed_{i}" for i, c in enumerate(rows[0])]
    body = pd.DataFrame([list(r) + [None] * (len(header) - len(r)) for r in rows[1:]], columns=header)
    donor_col = header[0]
    body[donor_col] = body[donor_col].astype(str).str.strip()
    out = pd.DataFrame({"donor_id": body[donor_col].map(normalise_donor_id), "donor_label_original": body[donor_col]})
    cols_meta, note_cols = [], []
    for h in header[1:]:
        series = body[h]
        if h.startswith("unnamed_"):
            if series.notna().any():
                note_cols.append(h)
            continue
        internal, desc, status = ENDPOINT_MAP.get(h, ("x_" + re.sub(r"[^0-9a-z]+", "_", h.lower()).strip("_"),
                                                      "Not in the explicit endpoint mapping; kept with an auto-generated name.", CONFIRM))
        num = pd.to_numeric(series, errors="coerce")
        out[internal] = num
        _, unit = split_name_unit(h)
        if unit is None and h.rstrip().endswith("%"):
            unit = "% (from header text)"
        cols_meta.append(dict(internal_name=internal, original_name=h, unit=unit or "none stated", description=desc,
                              interpretation_status=status, n_values=int(num.notna().sum()), n_missing=int(num.isna().sum()),
                              n_negative=int((num < 0).sum()), mapping="explicit" if h in ENDPOINT_MAP else "auto"))
        if (num < 0).any():
            res.issues.append(dict(severity="warning", check="endpoint_negative_values",
                                   message=f"{h}: {int((num < 0).sum())} negative value(s) for donors "
                                           f"{', '.join(out.loc[num < 0, 'donor_id'])}. Activity cannot be negative; probably blank-subtracted - "
                                           "kept unchanged, please confirm."))
        if series.notna().sum() < len(series):
            res.issues.append(dict(severity="warning", check="endpoint_missing_values",
                                   message=f"{h}: {int(series.isna().sum())} missing value(s)."))
    # free-text note column(s): keep verbatim, attached to the row they were written on
    out["row_note"] = ""
    for h in note_cols:
        txt = body[h].map(lambda v: "" if (v is None or pd.isna(v) or v == "") else str(v))   # blank cells -> ""
        out["row_note"] = (out["row_note"] + txt).astype(str)
        for i in np.flatnonzero(txt != ""):
            res.issues.append(dict(severity="warning", check="endpoint_row_note",
                                   message=f"Donor {out.loc[i, 'donor_id']} has an unlabelled note '{txt.iloc[i]}' in column {chr(65 + header.index(h))}. "
                                           "It is not known which measurement it refers to; kept verbatim, values unchanged."))
    dup = out["donor_id"].duplicated(keep=False)
    if dup.any():
        res.issues.append(dict(severity="error", check="endpoint_duplicate_donor",
                               message=f"Donor appears more than once: {', '.join(sorted(set(out.loc[dup, 'donor_id'])))}. "
                                       "Expected one row per donor."))
    else:
        res.issues.append(dict(severity="info", check="endpoint_one_row_per_donor",
                               message=f"{len(out)} donors, one row each (verified)."))
    out["endpoint_source"] = path.name
    res.table = out
    res.columns = pd.DataFrame(cols_meta)
    return res


def load_aliases(path: Path | None) -> dict[str, str]:
    """Explicit alias table: alias -> canonical donor_id (both normalised). Missing file = no aliases."""
    if path is None or not Path(path).exists():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not {"alias", "donor_id"} <= set(df.columns):
        return {}
    return {normalise_donor_id(a): normalise_donor_id(d) for a, d in zip(df["alias"], df["donor_id"]) if a and d}


def resolve_donor(label, aliases: dict[str, str]) -> str:
    """Canonical donor id for a label: exact match unless an explicit alias says otherwise."""
    n = normalise_donor_id(label)
    return aliases.get(n, n)


def link_endpoints_to_donors(endpoints: pd.DataFrame, donor_ids: list[str], aliases: dict[str, str] | None = None) -> pd.DataFrame:
    """Report, for each imaging donor id, whether an endpoint row exists (exact match or explicit alias only)."""
    aliases = aliases or {}
    ep_ids = set(endpoints["donor_id"]) if len(endpoints) else set()
    rows = []
    for d in sorted(set(donor_ids)):
        canon = resolve_donor(d, aliases)
        rows.append(dict(imaging_donor_id=d, endpoint_donor_id=canon if canon in ep_ids else None,
                         matched_by=("exact" if canon == normalise_donor_id(d) else "alias") if canon in ep_ids else "no endpoint row"))
    return pd.DataFrame(rows)
