"""Shared Streamlit pieces: settings, import controls, global filters, data loading, figure/table helpers."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from . import spec
from .config import DEFAULT_DATA_DIR, DEFAULT_MANIFEST, DEFAULT_PROCESSED_DIR
from .metadata import UNASSIGNED, write_manifest_template
from .store import Dataset, ingest, load_dataset
from .viz import PLOT_CONFIG, fig_to_png

EXPLORATORY = ("Exploratory view. Observations are repeated measures of the same cells, positions and donors; they are "
               "**not independent biological replicates**, and no result here has been tested for biological significance.")


def _token(processed: Path, manifest: Path) -> tuple:
    """Cheap 'version stamp' of the processed folder and manifest; when it changes the cached data is reloaded."""
    metas = sorted((processed / "samples").glob("*/meta.json")) if (processed / "samples").exists() else []
    mm = manifest.stat().st_mtime if manifest.exists() else 0
    return (tuple((str(p.parent.name), p.stat().st_mtime) for p in metas), mm)


@st.cache_resource(show_spinner="Loading processed tables…", max_entries=3)   # shared, never mutated -> no per-rerun copy
def _cached_load(processed: str, manifest: str, data_dir: str, token: tuple, exclude_dups: bool) -> Dataset:
    """Cached call to load_dataset so pages do not reload Parquet on every click."""
    return load_dataset(Path(processed), Path(manifest), exclude_duplicates=exclude_dups, data_dir=Path(data_dir))


@dataclass
class Ctx:
    ds: Dataset            # filtered dataset
    full: Dataset          # unfiltered
    data_dir: Path
    processed_dir: Path
    manifest_path: Path


def get_context() -> Ctx:
    """Sidebar: data location, import button, global filters. Returns the filtered dataset."""
    ss = st.session_state
    ss.setdefault("data_dir", str(DEFAULT_DATA_DIR))
    ss.setdefault("processed_dir", str(DEFAULT_PROCESSED_DIR))
    ss.setdefault("manifest_path", str(DEFAULT_MANIFEST))
    with st.sidebar:
        st.markdown("### HoloMonitor Explorer")
        with st.expander("Data location & import", expanded=("full_loaded" not in ss)):
            ss["data_dir"] = st.text_input("Data directory (searched recursively)", ss["data_dir"],
                                           help="Folder containing the -MOR / -TRACKING .xlsx exports. Files are only ever read.")
            ss["manifest_path"] = st.text_input("Manifest (optional CSV/XLSX)", ss["manifest_path"],
                                                help="Maps each position to donor / run / well / passage / culture date / fusion index.")
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
    processed, manifest, data_dir = Path(ss["processed_dir"]), Path(ss["manifest_path"]), Path(ss["data_dir"])
    full = _cached_load(str(processed), str(manifest), str(data_dir), _token(processed, manifest), ss.get("exclude_dups", True))
    ss["full_loaded"] = True
    if full.empty:
        return Ctx(full, full, data_dir, processed, manifest)

    s = full.samples
    s = s[s["status"].eq("ok")] if "status" in s else s
    s = s[s["duplicate_of"].eq("")] if ss.get("exclude_dups", True) and "duplicate_of" in s else s
    with st.sidebar:
        st.markdown("#### Filters")
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
        sel_f = st.multiselect("File / sample", list(label), default=list(label), format_func=lambda k: label[k])
        ids = [i for i in s["sample_id"] if i in sel_f]
        st.caption(f"{len(ids)} of {len(full.samples)} positions selected.")
        if any(d == UNASSIGNED for d in sel_d):
            st.caption("Donor shown as 'Unassigned' where no manifest entry exists.")
    return Ctx(full.subset(ids), full, data_dir, processed, manifest)


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
