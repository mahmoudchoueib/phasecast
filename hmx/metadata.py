"""Sample metadata: the configurable manifest and its application to imported samples.

Nothing is inferred from file names. ``donor_id`` stays "Unassigned" until the manifest provides it.
Well / position found *inside* the files ('Well A1 - Position 6') are used as a default, and a
manifest value that disagrees with the file is reported, not silently preferred.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

UNASSIGNED = "Unassigned"
MANIFEST_COLUMNS = ["sample_key", "donor_id", "run_id", "well_id", "position_id", "passage", "culture_date",
                    "fusion_index", "notes"]
HIERARCHY = ["donor_id", "run_id", "well_id", "position_id"]
REQUIRED_FOR_COMPLETE = ["donor_id", "run_id"]


def read_manifest(path: Path | None) -> tuple[pd.DataFrame, list[dict]]:
    """Read manifest CSV/XLSX. Returns (manifest, issues). Missing file => empty manifest, no error."""
    issues: list[dict] = []
    if path is None or not Path(path).exists():
        return pd.DataFrame(columns=MANIFEST_COLUMNS), issues
    p = Path(path)
    try:
        df = pd.read_excel(p, dtype=str) if p.suffix.lower() == ".xlsx" else pd.read_csv(p, dtype=str, keep_default_na=False)
    except Exception as exc:
        return pd.DataFrame(columns=MANIFEST_COLUMNS), [dict(severity="error", check="manifest_unreadable",
                                                             message=f"Manifest {p.name} could not be read: {exc}")]
    df.columns = [c.strip().lower() for c in df.columns]
    if "sample_key" not in df.columns:
        issues.append(dict(severity="error", check="manifest_columns",
                           message="Manifest needs a 'sample_key' column (e.g. A1P6 or subfolder/A1P6)."))
        return pd.DataFrame(columns=MANIFEST_COLUMNS), issues
    for c in MANIFEST_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[MANIFEST_COLUMNS].apply(lambda s: s.fillna("").astype(str).str.strip())
    df = df[df["sample_key"] != ""]
    dup = df["sample_key"].str.lower().duplicated(keep=False)
    if dup.any():
        issues.append(dict(severity="error", check="manifest_duplicate_keys",
                           message="Duplicate sample_key rows in manifest (first one is used): "
                                   + ", ".join(sorted(set(df.loc[dup, "sample_key"])))))
        df = df.drop_duplicates("sample_key", keep="first")
    bad_date = (df["culture_date"] != "") & pd.to_datetime(df["culture_date"], errors="coerce", dayfirst=False).isna()
    if bad_date.any():
        issues.append(dict(severity="warning", check="manifest_culture_date",
                           message="Unparseable culture_date (use YYYY-MM-DD) for: " + ", ".join(df.loc[bad_date, "sample_key"])))
    bad_fi = (df["fusion_index"] != "") & pd.to_numeric(df["fusion_index"], errors="coerce").isna()
    if bad_fi.any():
        issues.append(dict(severity="warning", check="manifest_fusion_index",
                           message="Non-numeric fusion_index for: " + ", ".join(df.loc[bad_fi, "sample_key"])))
    return df.reset_index(drop=True), issues


def apply_manifest(samples: pd.DataFrame, manifest: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Attach manifest metadata to the sample table. Returns (samples, issues)."""
    issues: list[dict] = []
    s = samples.copy()
    for c in ["donor_id", "run_id", "passage", "culture_date", "fusion_index", "notes"]:
        s[c] = ""
    s["well_id"] = s.get("well_id_file", "").fillna("")
    s["position_id"] = s.get("position_id_file", "").fillna("")
    s["metadata_source"] = "none (unassigned)"
    matched_keys: set[str] = set()
    if len(manifest):
        mk = {k.lower(): r for k, r in manifest.set_index("sample_key", drop=False).iterrows()}
        for i, row in s.iterrows():
            r = next((mk[k.lower()] for k in (row["sample_id"], row["key"]) if k and k.lower() in mk), None)
            if r is None:
                continue
            matched_keys.add(r["sample_key"].lower())
            s.at[i, "metadata_source"] = "manifest"
            for c in ["donor_id", "run_id", "passage", "culture_date", "fusion_index", "notes"]:
                s.at[i, c] = r[c]
            for c, file_c in (("well_id", "well_id_file"), ("position_id", "position_id_file")):
                if r[c]:
                    fv = row.get(file_c)
                    if fv and str(fv) != r[c]:
                        issues.append(dict(severity="warning", check="manifest_conflicts_with_file", sample_id=row["sample_id"],
                                           message=f"Manifest {c}='{r[c]}' differs from the value inside the file ('{fv}'). "
                                                   "Manifest value used; please confirm."))
                    s.at[i, c] = r[c]
        for k in manifest["sample_key"]:
            if k.lower() not in matched_keys:
                issues.append(dict(severity="warning", check="manifest_unmatched_row",
                                   message=f"Manifest row '{k}' matches no imported sample (check spelling / folder prefix)."))
    s["donor_id"] = s["donor_id"].replace("", UNASSIGNED)
    s["run_id"] = s["run_id"].replace("", UNASSIGNED)
    s["fusion_index"] = pd.to_numeric(s["fusion_index"], errors="coerce")
    s["culture_date"] = pd.to_datetime(s["culture_date"].replace("", pd.NA), errors="coerce")
    s["metadata_complete"] = (s["donor_id"] != UNASSIGNED) & (s["run_id"] != UNASSIGNED) & (s["well_id"] != "") & (s["position_id"] != "")
    for _, r in s[~s["metadata_complete"]].iterrows():
        missing = [c for c in HIERARCHY if r[c] in ("", UNASSIGNED)]
        issues.append(dict(severity="info", check="missing_identifiers", sample_id=r["sample_id"],
                           message=f"Sample is missing {', '.join(missing)}; shown as '{UNASSIGNED}' where applicable. "
                                   "Add a row to the manifest to resolve."))
    return s, issues


def endpoints_from_samples(samples: pd.DataFrame) -> pd.DataFrame:
    """Biological endpoints (currently fusion index from the manifest) - empty if none supplied."""
    e = samples.loc[samples["fusion_index"].notna(), ["sample_id", "donor_id", "run_id", "well_id", "position_id", "fusion_index"]]
    return e.reset_index(drop=True)


def write_manifest_template(path: Path, samples: pd.DataFrame) -> int:
    """Append rows for samples not yet in the manifest (never overwrites existing rows). Returns rows added."""
    path = Path(path)
    existing, _ = read_manifest(path)
    have = {k.lower() for k in existing["sample_key"]}
    new = [dict(sample_key=sid, donor_id="", run_id="", well_id=w or "", position_id=p or "", passage="", culture_date="",
                fusion_index="", notes="") for sid, w, p in zip(samples["sample_id"], samples.get("well_id_file", ""),
                                                                samples.get("position_id_file", ""))
           if sid.lower() not in have]
    if new:
        out = pd.concat([existing, pd.DataFrame(new)], ignore_index=True)[MANIFEST_COLUMNS]
        out.to_csv(path, index=False)
    return len(new)
