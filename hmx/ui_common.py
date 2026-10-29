"""Shared Streamlit pieces: settings, import controls, global filters, data loading, figure/table helpers."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from . import spec
from .branding import inject_css, sidebar_brand
from .config import DEFAULT_DATA_DIR, DEFAULT_MANIFEST, DEFAULT_META_DIR, DEFAULT_PROCESSED_DIR
from .mapping import UNASSIGNED, UNRESOLVED
from .store import Dataset, ingest, load_dataset
from .viz import PLOT_CONFIG, fig_to_png

EXPLORATORY = ("Exploratory view. Observations are repeated measures of the same cells, positions and donors; they are "
               "**not independent biological replicates**, and no result here has been tested for biological significance.")


def _token(processed: Path, manifest: Path, meta_dir: Path) -> tuple:
    """Cheap 'version stamp' of everything the loader reads; when it changes the cached data is reloaded.

    It covers the processed samples, the manifest and every editable metadata file (donor map, plates, ...),
    so saving a donor assignment in the Plate Mapper is picked up immediately.
    """
    metas = sorted((processed / "samples").glob("*/meta.json")) if (processed / "samples").exists() else []
    extra = [manifest] + (sorted(meta_dir.glob("*.csv")) if meta_dir.exists() else []) + \
            [processed / "inventory.parquet"] + (sorted((processed / "endpoints").glob("*")) if (processed / "endpoints").exists() else [])
    return (tuple((str(p.parent.name), p.stat().st_mtime) for p in metas),
            tuple((p.name, p.stat().st_mtime) for p in extra if p.exists()))


@st.cache_resource(show_spinner="Loading processed tables…", max_entries=3)   # shared, never mutated -> no per-rerun copy
def _cached_load(processed: str, manifest: str, data_dir: str, meta_dir: str, token: tuple, exclude_dups: bool) -> Dataset:
    """Cached call to load_dataset so pages do not reload Parquet on every click (`token` busts the cache on change)."""
    return load_dataset(Path(processed), Path(manifest), exclude_duplicates=exclude_dups, data_dir=Path(data_dir), meta_dir=Path(meta_dir))


@dataclass
class Ctx:
    """Everything a page needs. `ds` = what the sidebar filters select (analysis pages); `full` = everything
    imported, regardless of filters or eligibility (Plate Mapper, Inventory and mapping/QC pages)."""
    ds: Dataset
    full: Dataset
    data_dir: Path
    processed_dir: Path
    manifest_path: Path
    meta_dir: Path = DEFAULT_META_DIR
    n_hidden_ineligible: int = 0


def reload_data() -> None:
    """Drop the cached dataset so the next run re-reads processed files and metadata (call after saving metadata)."""
    _cached_load.clear()


def get_context() -> Ctx:
    """Sidebar: brand, data location, import button, global filters. Returns the filtered dataset.

    Filtering is a DataFrame operation: each selector narrows the table of imaging positions (`samples`),
    and `Dataset.subset` then keeps only the rows of every other table whose `sample_id` survived.
    """
    ss = st.session_state
    ss.setdefault("data_dir", str(DEFAULT_DATA_DIR))
    ss.setdefault("processed_dir", str(DEFAULT_PROCESSED_DIR))
    ss.setdefault("manifest_path", str(DEFAULT_MANIFEST))
    ss.setdefault("meta_dir", str(DEFAULT_META_DIR))
    inject_css()
    with st.sidebar:
        sidebar_brand()
        with st.expander("Data location & import", expanded=("full_loaded" not in ss)):
            ss["data_dir"] = st.text_input("Data directory (searched recursively)", ss["data_dir"],
                                           help="Folder containing the -MOR / -TRACKING .xlsx exports and the endpoint workbook. Files are only ever read.")
            ss["meta_dir"] = st.text_input("Mapping metadata folder", ss["meta_dir"],
                                           help="Where donor_map.csv, plates.csv, donor_aliases.csv and sample_resolution.csv are kept.")
            ss["manifest_path"] = st.text_input("Sample manifest (optional CSV/XLSX)", ss["manifest_path"],
                                                help="Optional per-position passage / culture date. Donor mapping is done in the Plate Mapper.")
            ss["processed_dir"] = st.text_input("Processed (Parquet) folder", ss["processed_dir"])
            force = st.checkbox("Force re-import of unchanged files", False)
            exclude_dups = st.checkbox("Exclude byte-identical duplicate imports", True)
            ss["exclude_dups"] = exclude_dups
            if st.button("Scan folder and import new / changed files", type="primary", width="stretch"):
                bar = st.progress(0.0, text="Scanning…")
                try:
                    rep = ingest(Path(ss["data_dir"]), Path(ss["processed_dir"]), force=force,
                                 progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
                    _cached_load.clear()
                    ss["last_report"] = rep
                    bar.empty()
                except Exception as exc:
                    bar.empty()
                    st.error(f"Import could not start: {exc}")
            rep = ss.get("last_report")
            if rep is not None:
                (st.warning if rep.failed or rep.problems else st.success)(rep.summary())
                for sid, err in rep.failed:
                    st.error(f"{sid}: {err}")
    processed, manifest, data_dir, meta_dir = Path(ss["processed_dir"]), Path(ss["manifest_path"]), Path(ss["data_dir"]), Path(ss["meta_dir"])
    full = _cached_load(str(processed), str(manifest), str(data_dir), str(meta_dir), _token(processed, manifest, meta_dir),
                        ss.get("exclude_dups", True))
    ss["full_loaded"] = True
    if full.empty:
        return Ctx(full, full, data_dir, processed, manifest, meta_dir)

    s = full.samples
    s = s[s["status"].eq("ok")] if "status" in s else s
    s = s[s["duplicate_of"].eq("")] if ss.get("exclude_dups", True) and "duplicate_of" in s else s
    n_before = len(s)
    with st.sidebar:
        st.markdown("#### Filters (analysis pages)")
        incl = st.toggle("Include unresolved / flagged positions", value=False,
                         help="Off (default): only positions whose donor is confirmed and that carry no unresolved flag "
                              "(label conflict, duplicate, ...) are analysed. Unresolved wells must stay out of donor-level "
                              "correlation and prediction work. The Plate Mapper and Inventory always show everything.")
        if not incl:
            s = s[s["analysis_eligible"]]
        n_hidden = n_before - len(s)
        plates = sorted(s["plate_label"].astype(str).unique())
        sel_pl = st.multiselect("Plate / experiment", plates, default=plates)
        s = s[s["plate_label"].astype(str).isin(sel_pl)]
        donors = sorted(s["donor_id"].astype(str).unique())
        sel_d = st.multiselect("Donor", donors, default=donors)
        s = s[s["donor_id"].astype(str).isin(sel_d)]
        wells = sorted(s["well_id"].astype(str).unique())
        sel_w = st.multiselect("Well", wells, default=wells)
        s = s[s["well_id"].astype(str).isin(sel_w)]
        poss = sorted(s["position_id"].astype(str).unique(), key=lambda v: (len(v), v))
        sel_p = st.multiselect("Position", poss, default=poss)
        s = s[s["position_id"].astype(str).isin(sel_p)]
        label = {r.sample_id: f"{r.sample_id}  ({r.morphology_file or '—'} | {r.tracking_file or '—'})" for r in s.itertuples()}
        with st.expander(f"File / sample ({len(label)})"):
            sel_f = st.multiselect("Files", list(label), default=list(label), format_func=lambda k: label[k], label_visibility="collapsed")
        ids = [i for i in s["sample_id"] if i in sel_f]
        st.caption(f"{len(ids)} of {n_before} positions selected.")
        if n_hidden:
            st.caption(f"{n_hidden} position(s) hidden: donor unresolved or flagged (see Plate Mapper / Data Quality and Mapping).")
    return Ctx(full.subset(ids), full, data_dir, processed, manifest, meta_dir, n_hidden)


def need_data(ctx: Ctx, need: str | None = None) -> bool:
    """Show a friendly message and return False when the page cannot be drawn."""
    if ctx.full.empty:
        st.info("No data imported yet. Put the `-MOR.xlsx` / `-TRACKING.xlsx` files in the data directory and click "
                "**Scan folder and import** in the sidebar.")
        return False
    if ctx.ds.samples.empty:
        st.warning("The current filters select no positions.")
        return False
    if need and getattr(ctx.ds, need).empty:
        st.warning(f"The selected positions have no {need.replace('_', ' ')} data (e.g. the matching file is missing).")
        return False
    return True


def header(title: str, subtitle: str | None = None) -> None:
    """Page title plus caption."""
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def csv_bytes(df: pd.DataFrame) -> bytes:
    """DataFrame -> CSV bytes for download buttons (UTF-8 with BOM so Excel shows µ correctly)."""
    return df.to_csv(index=False).encode("utf-8-sig")


def parquet_bytes(df: pd.DataFrame) -> bytes:
    """DataFrame -> Parquet bytes for download buttons."""
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def show_fig(fig, key: str, data: pd.DataFrame | None = None, name: str = "figure", png_button: bool = True) -> None:
    """Draw a Plotly figure with its toolbar, plus 'Plotted data (CSV)' and 'Prepare PNG' buttons."""
    st.plotly_chart(fig, width="stretch", config=PLOT_CONFIG, key=f"plot_{key}")
    cols = st.columns([1, 1, 4])
    if data is not None:
        cols[0].download_button("Plotted data (CSV)", csv_bytes(data), f"{name}.csv", "text/csv", key=f"csv_{key}")
    if png_button:
        if cols[1].button("Prepare PNG", key=f"pngb_{key}"):
            with st.spinner("Rendering PNG…"):
                png = fig_to_png(fig)
            if png:
                st.session_state[f"png_{key}"] = png
            else:
                cols[2].warning("PNG rendering needs Chrome/Edge for the 'kaleido' engine; use the camera icon in the plot toolbar instead.")
        if f"png_{key}" in st.session_state:
            cols[1].download_button("Download PNG", st.session_state[f"png_{key}"], f"{name}.png", "image/png", key=f"pngd_{key}")


def feature_options(df: pd.DataFrame, prefer: list[str] | None = None) -> list[str]:
    """Numeric columns that are known features (in spec order), then any other numeric column."""
    known = [f.internal for f in spec.CELL_FEATURES + spec.FRAME_ONLY_FEATURES + spec.TRACK_FEATURES if f.internal in df.columns]
    seen, out = set(), []
    for c in (prefer or []) + known:
        if c in df.columns and c not in seen and pd.api.types.is_numeric_dtype(df[c]):
            seen.add(c)
            out.append(c)
    return out


def fmt_label(c: str) -> str:
    """Axis-style label of a feature, e.g. 'Cell area (µm²)'."""
    return spec.label_of(c)
