"""Page 1 (Dataset Overview) and Page 7 (Quality Control and Data Dictionary)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import streamlit as st

from . import spec
from .metadata import MANIFEST_COLUMNS, UNASSIGNED, read_manifest, write_manifest_template
from .ui_common import Ctx, csv_bytes, header, need_data
from .validation import flag_records


def _fmt_dt(x) -> str:
    """Format a timestamp for a metric card."""
    return "—" if x is None or pd.isna(x) else pd.Timestamp(x).strftime("%d %b %Y %H:%M")


def overview(ctx: Ctx) -> None:
    """PAGE: Dataset Overview - counts, time span, missing values, warnings, searchable dataset table, manifest editor."""
    header("Dataset Overview", "What has been imported, how complete it is, and what to be careful about.")
    if not need_data(ctx):
        _discovery_panel(ctx)
        return
    ds, s = ctx.ds, ctx.ds.samples
    assigned = s.loc[s["donor_id"] != UNASSIGNED, "donor_id"].nunique()
    n_unassigned = int((s["donor_id"] == UNASSIGNED).sum())
    wells = s[["donor_id", "run_id", "well_id"]].astype(str).drop_duplicates().shape[0]
    c = st.columns(5)
    c[0].metric("Donors (identified)", assigned, help=f"{n_unassigned} position(s) have no donor assigned (shown as 'Unassigned'). "
                                                      "Donor IDs come only from the manifest, never from file names.")
    c[1].metric("Wells", wells)
    c[2].metric("Imaging positions", len(s))
    c[3].metric("Morphology files", int(s["morphology_file"].notna().sum()))
    c[4].metric("Tracking files", int(s["tracking_file"].notna().sum()))
    c = st.columns(5)
    c[0].metric("Frames", f"{len(ds.frames):,}")
    c[1].metric("Cell observations", f"{len(ds.cells):,}", help="Rows of 'All cells' (edge cells are excluded by the software).")
    n_tracks = len(ds.track_summary)
    c[2].metric("Tracked cells", f"{n_tracks:,}", help=f"{ds.track_summary['family'].nunique() if n_tracks else 0} families "
                                                      "(a family = founder cell and all its descendants).")
    c[3].metric("Divisions (tracking)", int(ds.track_summary["divides"].sum()) if n_tracks else 0)
    c[4].metric("Tracked observations", f"{len(ds.track_obs):,}")
    c = st.columns(4)
    c[0].metric("Earliest acquisition", _fmt_dt(s["acquisition_start"].pipe(pd.to_datetime, errors="coerce").min()))
    c[1].metric("Latest acquisition", _fmt_dt(s["acquisition_end"].pipe(pd.to_datetime, errors="coerce").max()))
    dur = s["duration_h"].max()
    c[2].metric("Imaging duration", f"{dur:.1f} h" if pd.notna(dur) else "—")
    iv = s["median_interval_min"].median()
    c[3].metric("Frame interval (median)", f"{iv:.1f} min" if pd.notna(iv) else "—")

    st.subheader("Data-quality warnings")
    iss = ds.issues if not ctx.ds.issues.empty else ctx.full.issues
    iss = iss[iss["severity"].isin(["warning", "error"])] if len(iss) else iss
    if iss.empty:
        st.success("No warnings or errors were recorded for the selected positions.")
    else:
        vc = iss["severity"].value_counts()
        st.warning(f"{vc.get('error', 0)} error(s), {vc.get('warning', 0)} warning(s). Full detail on the *Quality Control* page.")
        st.dataframe(iss[["sample_id", "severity", "check", "message"]].head(12), hide_index=True, width="stretch")

    st.subheader("Missing-value summary")
    rows = []
    for name in ("frames", "cells", "track_obs", "track_summary"):
        df = getattr(ds, name)
        if df.empty:
            continue
        num = df.select_dtypes("number")
        miss = num.isna().mean() * 100
        rows.append(dict(table=name, rows=len(df), numeric_columns=num.shape[1],
                         columns_with_missing=int((miss > 0).sum()), worst_column=(miss.idxmax() if (miss > 0).any() else "—"),
                         worst_missing_pct=round(float(miss.max()), 2)))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Tracking tables contain structural gaps by design (e.g. `parent_id` is empty for founder cells; "
               "`daughters_start_s` for non-dividing cells). Column-level detail: *Quality Control → Missingness*.")

    st.subheader("Imported datasets")
    q = st.text_input("Search (any column)", "", placeholder="e.g. A1P6, Unassigned, Position 6")
    cols = ["sample_id", "donor_id", "run_id", "well_id", "position_id", "morphology_file", "tracking_file", "n_frames", "n_cell_obs",
            "n_tracks", "duration_h", "median_interval_min", "acquisition_start", "link_pct", "metadata_source", "experiment_name",
            "group_label", "exported_at_morphology", "exported_at_tracking"]
    t = s[[c for c in cols if c in s]].copy()
    if q:
        t = t[t.astype(str).apply(lambda r: r.str.contains(q, case=False, regex=False).any(), axis=1)]
    st.dataframe(t, hide_index=True, width="stretch")
    st.download_button("Download dataset table (CSV)", csv_bytes(t), "datasets.csv", "text/csv")
    st.caption("`experiment_name` / `group_label` are free text written by the HoloMonitor software; they are displayed but "
               "not interpreted as donor or plate identifiers.")
    _manifest_panel(ctx)
    _discovery_panel(ctx)


def _manifest_panel(ctx: Ctx) -> None:
    """Editable donor/run/well/passage/culture-date/fusion-index table that is saved to manifest.csv."""
    with st.expander("Sample metadata (manifest): donor / run / well / position / passage / culture date / fusion index"):
        st.write("The manifest assigns metadata to each position **without touching any Python code or source file**. "
                 "`sample_key` is the sample id shown above (for `A1P6-MOR.xlsx` + `A1P6-TRACKING.xlsx` in the data folder: `A1P6`; "
                 "inside a sub-folder: `subfolder/A1P6`). Leave `fusion_index` empty until real endpoint values exist.")
        man, issues = read_manifest(ctx.manifest_path)
        for it in issues:
            st.warning(it["message"])
        keys = list(ctx.full.samples["sample_id"])
        base = man.copy()
        missing = [k for k in keys if k.lower() not in {x.lower() for x in base["sample_key"]}]
        add = pd.DataFrame([dict(sample_key=k, donor_id="", run_id="", well_id="", position_id="", passage="", culture_date="",
                                 fusion_index="", notes="") for k in missing])
        edit = pd.concat([base, add], ignore_index=True)[MANIFEST_COLUMNS]
        out = st.data_editor(edit, num_rows="dynamic", hide_index=True, width="stretch", key="manifest_editor")
        c = st.columns([1, 1, 3])
        if c[0].button("Save manifest"):
            out.to_csv(ctx.manifest_path, index=False)
            st.cache_resource.clear()
            st.success(f"Saved {ctx.manifest_path.name}. Reloading…")
            st.rerun()
        c[1].download_button("Download manifest", csv_bytes(out), "manifest.csv", "text/csv")


def _discovery_panel(ctx: Ctx) -> None:
    """Expander listing unmatched, ambiguous or unrecognised files."""
    p = ctx.processed_dir / "discovery.json"
    if p.exists():
        probs = json.loads(p.read_text(encoding="utf-8"))
        if probs:
            with st.expander(f"File discovery problems ({len(probs)}) - need manual resolution", expanded=False):
                st.dataframe(pd.DataFrame(probs), hide_index=True, width="stretch")


# ------------------------------------------------------------------------------------------------
def quality_control(ctx: Ctx) -> None:
    """PAGE: Quality Control and Data Dictionary (tabs: issues, missingness, duplicates, flagged records, import log, dictionary)."""
    header("Quality Control and Data Dictionary",
           "Checks only report; **no record is ever removed or altered**. Outliers are flagged for inspection, not discarded.")
    if not need_data(ctx):
        return
    ds = ctx.ds
    tabs = st.tabs(["Issues", "Missingness", "Duplicates & intervals", "Flagged records", "Pairing & import log", "Data dictionary"])
    iss = ctx.full.issues.copy()
    iss = iss[iss["sample_id"].isin(list(ds.samples["sample_id"]) + ["(manifest)"])] if len(iss) else iss

    with tabs[0]:
        if iss.empty:
            st.success("No issues recorded.")
        else:
            c = st.columns(3)
            sev = c[0].multiselect("Severity", ["error", "warning", "info"], default=["error", "warning"])
            checks = sorted(iss["check"].dropna().unique())
            chk = c[1].multiselect("Check", checks, default=checks)
            q = c[2].text_input("Search message")
            v = iss[iss["severity"].isin(sev) & iss["check"].isin(chk)]
            if q:
                v = v[v["message"].str.contains(q, case=False, regex=False)]
            cols = [c for c in ["sample_id", "severity", "check", "table", "n_records", "message"] if c in v]
            st.dataframe(v[cols], hide_index=True, width="stretch")
            vc = iss["severity"].value_counts()
            st.caption(f"All recorded: {vc.get('error', 0)} errors, {vc.get('warning', 0)} warnings, {vc.get('info', 0)} informational checks "
                       "(informational rows are checks that passed, e.g. reconciliations).")
            report = _markdown_report(ctx, iss)
            c = st.columns(3)
            c[0].download_button("QC report (CSV)", csv_bytes(iss), "qc_report.csv", "text/csv")
            c[1].download_button("QC report (Markdown)", report.encode("utf-8"), "qc_report.md", "text/markdown")

    with tabs[1]:
        tbl = st.selectbox("Table", [t for t in ("cells", "frames", "track_obs", "track_summary") if not getattr(ds, t).empty])
        df = getattr(ds, tbl)
        m = pd.DataFrame({"missing": df.isna().sum(), "missing_pct": df.isna().mean() * 100, "dtype": df.dtypes.astype(str)})
        m.index.name = "column"
        m = m.reset_index().sort_values("missing_pct", ascending=False)
        st.dataframe(m, hide_index=True, width="stretch")
        st.caption("Empty strings (e.g. an unclassified tracking End status) are not NaN and are reported under *Issues*.")

    with tabs[2]:
        f = ds.frames
        if len(f) and "elapsed_s" in f:
            fr = f.sort_values(["sample_id", "frame_no"]).copy()
            fr["interval_min"] = fr.groupby("sample_id", observed=True)["elapsed_s"].diff() / 60
            med = fr.groupby("sample_id", observed=True)["interval_min"].transform("median")
            fr["deviation_from_median_pct"] = 100 * (fr["interval_min"] - med) / med
            st.write("**Frame intervals** (morphology timestamps have 1-second resolution)")
            c = st.columns(4)
            c[0].metric("Median interval (min)", f"{fr['interval_min'].median():.2f}")
            c[1].metric("Shortest (min)", f"{fr['interval_min'].min():.2f}")
            c[2].metric("Longest (min)", f"{fr['interval_min'].max():.2f}")
            thr = st.slider("Flag intervals deviating more than (%)", 1, 50, 10)
            bad = fr[fr["deviation_from_median_pct"].abs() > thr]
            c[3].metric("Irregular intervals", len(bad))
            if len(bad):
                st.dataframe(bad[["sample_id", "frame_no", "elapsed_h", "interval_min", "deviation_from_median_pct"]], hide_index=True, width="stretch")
            dup_f = int(f.duplicated(["sample_id", "frame_no"]).sum())
            dup_t = int(f.duplicated(["sample_id", "acquired_at"]).sum()) if "acquired_at" in f else 0
            dup_c = int(ds.cells.duplicated(["sample_id", "frame_no", "cell_id"]).sum()) if len(ds.cells) else 0
            st.write("**Duplicates**")
            c = st.columns(3)
            c[0].metric("Duplicate frames", dup_f)
            c[1].metric("Repeated timestamps", dup_t)
            c[2].metric("Duplicate (frame, cell) rows", dup_c)

    with tabs[3]:
        if ds.cells.empty:
            st.info("No cell-level morphology loaded.")
        else:
            feats = st.multiselect("Features to screen", [f.internal for f in spec.CELL_FEATURES if f.internal in ds.cells],
                                   default=["area_um2", "perimeter_um", "hull_convexity", "shape_convexity", "irregularity", "eccentricity",
                                            "optical_thickness_avg_um", "optical_volume_um3"], format_func=spec.label_of)
            fl = flag_records(ds.cells, feats)
            st.caption("Flags: value missing/non-finite; outside a heuristic expected range (e.g. convexity > 1); or a robust outlier "
                       "(|z| > 8 using median/MAD within each position). Flagged records stay in every analysis.")
            if fl.empty:
                st.success("No records flagged for the selected features.")
            else:
                st.dataframe(fl.assign(feature=fl["feature"].map(lambda x: spec.label_of(x, False))), hide_index=True, width="stretch")
                st.write(fl.groupby(["feature", "reason"]).size().rename("n").reset_index())
                st.download_button("Flagged records (CSV)", csv_bytes(fl), "flagged_records.csv", "text/csv")
            if len(ds.track_obs):
                st.write("**Tracking: unclassified or unusual tracks**")
                ts = ds.track_summary
                odd = ts[(ts["end_status"].fillna("") == "") | (ts["n_frame_gaps"] > 0) | (ts["n_obs"] == 1)]
                st.dataframe(odd[["sample_id", "track_id", "n_obs", "start_s", "end_s", "end_status", "n_frame_gaps"]], hide_index=True, width="stretch")

    with tabs[4]:
        p = ctx.processed_dir / "discovery.json"
        probs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        st.write("**Discovery: unmatched / ambiguous / unrecognised files**")
        st.dataframe(pd.DataFrame(probs) if probs else pd.DataFrame({"result": ["No discovery problems."]}), hide_index=True, width="stretch")
        st.write("**Samples**")
        sm = ctx.full.samples
        st.dataframe(sm[[c for c in ["sample_id", "status", "morphology_file", "tracking_file", "source_present", "duplicate_of", "metadata_source"] if c in sm]],
                     hide_index=True, width="stretch")
        st.write("**Import log** (most recent first)")
        log = ctx.full.import_log
        if len(log):
            st.dataframe(log.iloc[::-1].head(200), hide_index=True, width="stretch")

    with tabs[5]:
        d = ctx.full.dictionary
        c = st.columns([2, 1, 1])
        q = c[0].text_input("Search the data dictionary", placeholder="e.g. convexity, µm, tracking, requires confirmation")
        tbl = c[1].multiselect("Table", sorted(d["table"].unique()), default=sorted(d["table"].unique()))
        stat = c[2].multiselect("Interpretation", sorted(d["interpretation_status"].unique()), default=sorted(d["interpretation_status"].unique()))
        v = d[d["table"].isin(tbl) & d["interpretation_status"].isin(stat)]
        if q:
            v = v[v.astype(str).apply(lambda r: r.str.contains(q, case=False, regex=False).any(), axis=1)]
        st.dataframe(v, hide_index=True, width="stretch")
        n_conf = int((d["interpretation_status"] == spec.CONFIRM).sum())
        st.caption(f"{n_conf} entries are marked '{spec.CONFIRM}': the export does not define them and nothing was assumed beyond what the "
                   "column name and the data themselves support. Original names and units are preserved in `original_name` / `unit`.")
        c = st.columns(3)
        c[0].download_button("Dictionary (CSV)", csv_bytes(d), "data_dictionary.csv", "text/csv")
        c[1].download_button("Dictionary (JSON)", json.dumps(d.to_dict("records"), indent=1, ensure_ascii=False).encode("utf-8"),
                             "data_dictionary.json", "application/json")


def _markdown_report(ctx: Ctx, iss: pd.DataFrame) -> str:
    """Build the downloadable QC report as Markdown text."""
    s = ctx.ds.samples
    lines = ["# HoloMonitor QC report", "",
             f"Positions: {len(s)} | frames: {len(ctx.ds.frames)} | cell observations: {len(ctx.ds.cells)} | tracks: {len(ctx.ds.track_summary)}", "",
             "Nothing was removed or modified. Info rows are checks that passed.", ""]
    for sev in ("error", "warning", "info"):
        sub = iss[iss["severity"] == sev]
        lines.append(f"## {sev.title()} ({len(sub)})")
        for r in sub.itertuples():
            lines.append(f"- **{r.sample_id}** `{r.check}`: {r.message}")
        lines.append("")
    return "\n".join(lines)
