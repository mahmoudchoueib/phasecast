"""PhaseCast pages for data foundations: Research Overview, Plate Mapper, Dataset Inventory,
Endpoint Data Preview, and Data Quality and Mapping.

How the pages get their data
----------------------------
Every page receives a ``Ctx`` (see ui_common). ``ctx.full`` holds EVERY imported position, including
unresolved or flagged ones, which is what these pages need: you cannot map a plate if the unresolved wells
are hidden. The analysis pages use ``ctx.ds`` (filtered, eligible positions by default).

A *DataFrame* is a table. Most functions here start from the ``samples`` table (one row per imaging position),
use ``groupby`` to collapse positions into wells or donors, and ``merge`` (a join, like a spreadsheet VLOOKUP
on a key column) to attach donor information to wells.
"""
from __future__ import annotations

import json
import string

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .branding import (AMBER, EMPTY_WELL_COLOUR, NAVY, TEAL, UNASSIGNED_COLOUR, UNRESOLVED_COLOUR, chip, donor_colour_map, hero)
from .endpoints import link_endpoints_to_donors, load_aliases
from .mapping import (CONFIRMED, PLATE_FORMATS, RESOLUTION_ACTIONS, UNASSIGNED, UNRESOLVED, clear_donor_assignments, donor_summary,
                      folder_donor_labels, load_donor_map, load_resolutions, save_donor_assignments, save_plate, save_resolution)
from .ui_common import Ctx, csv_bytes, header, need_data, reload_data
from .viz import PALETTE, style

STATUS_TEXT = {"confirmed_manual": "confirmed (manual)", "confirmed_folder": "confirmed (folder label)", "unresolved": "UNRESOLVED",
               "conflict": "CONFLICT", "unassigned": "unassigned"}


def _ok_samples(ctx: Ctx) -> pd.DataFrame:
    """All successfully imported positions (eligible or not)."""
    s = ctx.full.samples
    s = s[s["status"].eq("ok")] if "status" in s else s
    return s[s["duplicate_of"].eq("")] if "duplicate_of" in s else s


def _donor_universe(ctx: Ctx) -> list[str]:
    """Every donor id we know about: endpoint rows + donors that occur in the mapping. Used for stable colours."""
    ep = list(ctx.full.endpoints["donor_id"]) if len(ctx.full.endpoints) else []
    mapped = [d for d in ctx.full.samples["donor_id"].unique() if d not in (UNASSIGNED, UNRESOLVED)]
    return sorted(set(ep) | set(mapped))


# ================================================================================================
# Research Overview
# ================================================================================================
def research_overview(ctx: Ctx) -> None:
    """PAGE: Research Overview - the project question, data readiness per donor and what needs a decision."""
    hero("PhaseCast", "Forecasting cell potency: can early quantitative phase imaging of human skeletal muscle-derived cells "
                      "predict later myogenic fusion potential?")
    if not need_data(ctx):
        return
    full, s = ctx.full, _ok_samples(ctx)
    wells = full.wells
    ep = full.endpoints
    n_donors_ep = ep["donor_id"].nunique() if len(ep) else 0
    imaged = sorted(d for d in s.loc[s["mapping_status"].isin(CONFIRMED), "donor_id"].unique())
    c = st.columns(6)
    c[0].metric("Donors with endpoints", n_donors_ep)
    c[1].metric("Donors with confirmed imaging", len(imaged), help="Donor confirmed for at least one well (folder label or manual).")
    c[2].metric("Plates / experiments", s["plate_id"].nunique())
    c[3].metric("Wells with imaging", len(wells))
    c[4].metric("Imaging positions", f"{int(s['analysis_eligible'].sum())} / {len(s)}", help="analysis-eligible / all imported")
    c[5].metric("Frames", f"{int(s['n_frames'].sum()):,}")

    st.markdown("#### Project pipeline")
    stages = [
        ("1 · Ingest", f"{len(s)} MOR/TRACKING positions imported and cached", "ok"),
        ("2 · Map donors", f"{int((wells['mapping_status'].isin(CONFIRMED)).sum())}/{len(wells)} wells confirmed; "
                           f"{int((~wells['mapping_status'].isin(CONFIRMED)).sum())} need a decision", "warn" if (~wells["mapping_status"].isin(CONFIRMED)).any() else "ok"),
        ("3 · Quality", f"{int((~s['analysis_eligible']).sum())} positions flagged and held out", "warn" if (~s["analysis_eligible"]).any() else "ok"),
        ("4 · Explore", "morphology, motility, distributions (existing pages)", "ok"),
        ("5 · Early-window features", "per position/donor summaries (existing page)", "ok"),
        ("6 · Forecast models", "not started (planned)", "info"),
    ]
    cols = st.columns(len(stages))
    for col, (t, d, k) in zip(cols, stages):
        col.markdown(f'<div class="pc-card"><h4>{t}</h4><p>{d}</p><div style="margin-top:8px">{chip({"ok": "ready", "warn": "needs attention", "info": "planned"}[k], k)}</div></div>',
                     unsafe_allow_html=True)

    st.markdown("#### Donor readiness")
    ds_tab = donor_summary(s)
    ready = pd.DataFrame({"donor_id": sorted(set(ep["donor_id"]) | set(ds_tab["donor_id"] if len(ds_tab) else []))})
    ready = ready.merge(ds_tab, on="donor_id", how="left")
    ready["endpoint_row"] = ready["donor_id"].isin(set(ep["donor_id"]))
    for col in ("wells", "imaging_positions", "MOR_files", "TRACKING_files", "eligible_positions", "plates"):
        ready[col] = ready[col].fillna(0).astype(int)
    ready["mapping_status"] = ready["mapping_status"].fillna("no imaging mapped")
    if len(ep):
        ready = ready.merge(ep[["donor_id", "fi_pct"]] if "fi_pct" in ep else ep[["donor_id"]], on="donor_id", how="left")
    order = [c for c in ["donor_id", "endpoint_row", "fi_pct", "plates", "wells", "imaging_positions", "eligible_positions", "MOR_files", "TRACKING_files", "mapping_status"] if c in ready]
    st.dataframe(ready[order], hide_index=True, width="stretch",
                 column_config={"endpoint_row": st.column_config.CheckboxColumn("Endpoint row"), "fi_pct": st.column_config.NumberColumn("FI (%)", format="%.2f")})
    st.caption("Donors listed as 'Unresolved' or 'Unassigned' are not donors: they are wells whose donor is not yet known (see Plate Mapper).")

    st.markdown("#### Needs a decision")
    items = []
    unres = wells[wells["mapping_status"] == "unresolved"]
    if len(unres):
        items.append(f"**{len(unres)} wells have an unresolved donor** (candidate donors: {', '.join(sorted(set(', '.join(unres['candidates']).split(', '))))}). "
                     "Assign them in the *Plate Mapper* once the layout is confirmed.")
    flagged = s[~s["analysis_eligible"] & s["mapping_status"].isin(CONFIRMED)]
    if len(flagged):
        items.append(f"**{len(flagged)} positions with a confirmed donor are held out** because of file/label problems "
                     f"({', '.join(sorted(set(flagged['eligibility_reason'])))[:260]}). Review them in *Data Quality and Mapping*.")
    if len(full.endpoint_issues):
        w = full.endpoint_issues[full.endpoint_issues["severity"] == "warning"]
        if len(w):
            items.append(f"**{len(w)} endpoint-workbook warnings** (e.g. negative enzyme values, an unexplained note). See *Endpoint Data Preview*.")
    geom = full.plates[full.plates["geometry_status"] != "user_confirmed"] if len(full.plates) else pd.DataFrame()
    if len(geom):
        items.append(f"**Plate geometry is only suggested for {len(geom)} plate(s)** - confirm it in the Plate Mapper.")
    if items:
        for it in items:
            st.markdown(f'<div class="pc-note">{it}</div>', unsafe_allow_html=True)
            st.write("")
    else:
        st.success("Nothing needs a decision.")
    st.caption("PhaseCast is exploratory: it does not yet fit prediction models, and no correlation with endpoints is computed on these pages.")


# ================================================================================================
# Plate Mapper
# ================================================================================================
def _plate_grid(plate_samples: pd.DataFrame, wells: pd.DataFrame, plate_id: str, n_rows: int, n_cols: int) -> pd.DataFrame:
    """One row per well of the plate grid (including empty wells) with counts, donor and status."""
    g = plate_samples.groupby("well_id")
    agg = pd.DataFrame({
        "n_pos": g.size(),
        "n_mor": g["morphology_file"].apply(lambda x: int(x.notna().sum())),
        "n_trk": g["tracking_file"].apply(lambda x: int(x.notna().sum())),
        "frames": g["n_frames"].apply(lambda x: ", ".join(str(int(v)) for v in sorted(set(x.dropna())))),
        "dates": g["acquisition_start"].apply(lambda x: ", ".join(sorted({str(v)[:10] for v in x.dropna()}))),
        "n_flagged": g["flags"].apply(lambda x: int((x.fillna("") != "").sum())),
        "n_ineligible": g["analysis_eligible"].apply(lambda x: int((~x).sum())),
    }).reset_index()
    wl = wells[wells["plate_id"] == plate_id][["well_id", "donor_id", "mapping_status", "evidence", "candidates"]]
    rows = []
    for r in range(n_rows):
        for c in range(1, n_cols + 1):
            rows.append(dict(well_id=f"{string.ascii_uppercase[r]}{c}", row_idx=r, col_idx=c))
    grid = pd.DataFrame(rows).merge(agg, on="well_id", how="left").merge(wl, on="well_id", how="left")
    grid["n_pos"] = grid["n_pos"].fillna(0).astype(int)
    for c in ("n_mor", "n_trk", "n_flagged", "n_ineligible"):
        grid[c] = grid[c].fillna(0).astype(int)
    grid["donor_id"] = grid["donor_id"].fillna("")
    grid["mapping_status"] = grid["mapping_status"].fillna("no_data")
    grid["missing_files"] = (grid["n_pos"] > 0) & ((grid["n_mor"] < grid["n_pos"]) | (grid["n_trk"] < grid["n_pos"]))
    return grid


def _plate_figure(grid: pd.DataFrame, colours: dict[str, str], highlight: str | None, selected: str | None, n_rows: int, n_cols: int) -> go.Figure:
    """Draw the plate: one marker per well, coloured by donor. Red ring = a MOR or TRACKING file is missing,
    amber ring = flagged position(s) in the well, black ring = selected well."""
    size = int(max(18, min(60, 640 / max(n_cols, n_rows * 1.5) * 0.62)))
    fig = go.Figure()

    def cat(r) -> str:
        if r.n_pos == 0:
            return "No data"
        if r.mapping_status in CONFIRMED:
            return r.donor_id
        return "Unresolved" if r.mapping_status in ("unresolved", "conflict") else "Unassigned"

    grid = grid.assign(cat=[cat(r) for r in grid.itertuples()])
    order = [c for c in sorted(grid["cat"].unique()) if c not in ("No data", "Unresolved", "Unassigned")] + \
            [c for c in ("Unresolved", "Unassigned", "No data") if c in set(grid["cat"])]
    for name in order:
        d = grid[grid["cat"] == name]
        colour = colours.get(name, {"Unresolved": UNRESOLVED_COLOUR, "Unassigned": UNASSIGNED_COLOUR, "No data": EMPTY_WELL_COLOUR}.get(name, UNRESOLVED_COLOUR))
        dim = bool(highlight) and name != highlight
        line_col = np.where(d["missing_files"], "#DC2626", np.where(d["n_flagged"] > 0, AMBER, "rgba(255,255,255,0.9)"))
        line_w = np.where(d["missing_files"] | (d["n_flagged"] > 0), 4, 1.5)
        hover = [f"<b>{r.well_id}</b><br>Donor: {r.donor_id or '—'} ({STATUS_TEXT.get(r.mapping_status, r.mapping_status)})"
                 f"<br>Positions: {r.n_pos} · MOR {r.n_mor} · TRACKING {r.n_trk}<br>Frames: {r.frames or '—'}<br>Acquired: {r.dates or '—'}"
                 + (f"<br>Candidates: {r.candidates}" if r.candidates else "") + (f"<br><b>{r.n_flagged} flagged position(s)</b>" if r.n_flagged else "")
                 + ("<br><b>Missing MOR/TRACKING file</b>" if r.missing_files else "") for r in d.itertuples()]
        fig.add_trace(go.Scatter(
            x=d["col_idx"], y=d["row_idx"], mode="markers+text", name=name, text=[f"{r.well_id}" for r in d.itertuples()],
            textfont=dict(color="#0B1F3A" if name == "No data" else "#fff", size=max(9, size // 4)),
            marker=dict(size=size, color=colour, opacity=0.2 if dim else 1.0, symbol="circle" if name != "No data" else "circle-open",
                        line=dict(color=line_col, width=line_w)),
            hovertext=hover, hoverinfo="text", customdata=d["well_id"]))
    if selected and (grid["well_id"] == selected).any():
        r = grid[grid["well_id"] == selected].iloc[0]
        fig.add_trace(go.Scatter(x=[r.col_idx], y=[r.row_idx], mode="markers", showlegend=False, hoverinfo="skip",
                                 marker=dict(size=size + 14, color="rgba(0,0,0,0)", line=dict(color=NAVY, width=4))))
    style(fig, None, None, None, int(150 + n_rows * (size + 26)))
    fig.update_xaxes(tickmode="array", tickvals=list(range(1, n_cols + 1)), range=[0.4, n_cols + 0.6], showgrid=False, showline=False, side="top")
    fig.update_yaxes(tickmode="array", tickvals=list(range(n_rows)), ticktext=list(string.ascii_uppercase[:n_rows]),
                     range=[n_rows - 0.4, -0.6], showgrid=False, showline=False, scaleanchor="x", scaleratio=1)
    fig.update_layout(margin=dict(l=40, r=10, t=40, b=10), legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01, title="Donor"))
    return fig


def _selected_from_event(event) -> tuple[int, int] | None:
    """Extract the clicked marker's (column, row) from a Streamlit plotly selection event."""
    try:
        sel = event["selection"] if isinstance(event, dict) else event.selection
        pts = sel["points"] if isinstance(sel, dict) else sel.points
        if pts:
            p = pts[-1]
            return int(round(p["x"])), int(round(p["y"]))
    except Exception:
        return None
    return None


def plate_mapper(ctx: Ctx) -> None:
    """PAGE: Plate Mapper - visual plate layouts, donor colours, well details, and manual (audited) donor assignment."""
    hero("Plate Mapper", "See which wells belong to which donor, which files exist for each well, and resolve what is still unknown.")
    if not need_data(ctx):
        return
    full, s = ctx.full, _ok_samples(ctx)
    plates = full.plates.copy()
    ss = st.session_state
    labels = dict(zip(plates["plate_id"], plates["plate_label"]))
    top = st.columns([2, 3])
    plate_id = top[0].selectbox("Experiment / plate", list(labels), format_func=lambda p: f"{labels[p]}", key="pm_plate")
    prow = plates[plates["plate_id"] == plate_id].iloc[0]
    top[1].markdown(f"**Experiment name in the files:** `{prow['experiment_name']}`  \n**Plate id:** `{plate_id}`")

    # ---- geometry (confirmed by the user, never silently assumed) -------------------------------------
    n_rows, n_cols = int(prow["n_rows"]), int(prow["n_cols"])
    confirmed = prow["geometry_status"] == "user_confirmed"
    with st.expander(("Plate geometry: confirmed " if confirmed else "Plate geometry: NOT confirmed - ") + f"{n_rows} rows × {n_cols} columns",
                     expanded=not confirmed):
        if not confirmed:
            st.markdown(f'<div class="pc-note">The files record only the well label (e.g. A1), not the plate format. '
                        f'PhaseCast suggests <b>{prow["suggested"]}</b>, the smallest standard plate that contains every observed well. '
                        f'Please confirm or change it.</div>', unsafe_allow_html=True)
        fmt_opts = {f"{n}-well ({r}×{c})": (r, c) for n, (r, c) in PLATE_FORMATS.items()}
        cur = next((k for k, v in fmt_opts.items() if v == (n_rows, n_cols)), "Custom")
        choice = st.selectbox("Plate format", list(fmt_opts) + ["Custom"], index=(list(fmt_opts) + ["Custom"]).index(cur), key=f"pm_fmt_{plate_id}")
        if choice == "Custom":
            n_rows = st.number_input("Rows", 1, 26, n_rows, key=f"pm_r_{plate_id}")
            n_cols = st.number_input("Columns", 1, 48, n_cols, key=f"pm_c_{plate_id}")
        else:
            n_rows, n_cols = fmt_opts[choice]
        label_in = st.text_input("Plate label", prow["plate_label"], key=f"pm_lbl_{plate_id}")
        if st.button("Confirm geometry and label", key=f"pm_geo_{plate_id}"):
            save_plate(ctx.meta_dir, plate_id, label_in, prow["experiment_name"], int(n_rows), int(n_cols), "user_confirmed")
            reload_data()
            st.rerun()
    n_rows, n_cols = int(n_rows), int(n_cols)

    plate_samples = s[s["plate_id"] == plate_id]
    outside = [w for w in plate_samples["well_id"].unique() if not (len(w) >= 2 and w[0] in string.ascii_uppercase[:n_rows] and w[1:].isdigit() and 1 <= int(w[1:]) <= n_cols)]
    if outside:
        st.error(f"Wells outside the chosen geometry (not drawn): {', '.join(sorted(outside))}. Adjust the plate format.")
    grid = _plate_grid(plate_samples, full.wells, plate_id, n_rows, n_cols)

    universe = _donor_universe(ctx)
    colours = donor_colour_map(universe)
    on_plate = sorted(d for d in plate_samples["donor_id"].unique() if d in colours)
    hl_options = ["(none)"] + on_plate
    hl = st.selectbox("Highlight a donor (dims all other wells)", hl_options, key=f"pm_hl_{plate_id}")
    hl = None if hl == "(none)" else hl
    wells_here = sorted(plate_samples["well_id"].unique(), key=lambda w: (w[0], int(w[1:]) if w[1:].isdigit() else 0))
    sel_key = f"pm_sel_{plate_id}"
    if ss.get(sel_key) not in wells_here:
        ss[sel_key] = wells_here[0] if wells_here else None
    selected = ss[sel_key]

    left, right = st.columns([3, 2], gap="large")
    with left:
        fig = _plate_figure(grid, colours, hl, selected, n_rows, n_cols)
        event = st.plotly_chart(fig, width="stretch", key=f"pm_chart_{plate_id}_{selected}", on_select="rerun", selection_mode="points",
                                config={"displaylogo": False, "displayModeBar": False})
        clicked = _selected_from_event(event)
        if clicked:
            cc, rr = clicked
            well = f"{string.ascii_uppercase[rr]}{cc}" if 0 <= rr < n_rows else None
            if well and well in wells_here and well != selected:
                ss[sel_key] = well
                st.rerun()
        st.caption("Ring colours: **red** = a MOR or TRACKING file is missing for a position in that well · **amber** = flagged position(s) · "
                   "dark ring = selected well. Grey = donor unknown. Click a well, or pick it below.")
        pick = st.selectbox("Selected well", wells_here, index=wells_here.index(selected) if selected in wells_here else 0, key=f"pm_pick_{plate_id}_{selected}")
        if pick != selected:
            ss[sel_key] = pick
            st.rerun()
    with right:
        _well_details(plate_samples, grid, selected, ctx, plate_id, prow["plate_label"])

    st.markdown("---")
    _donor_panel(ctx, s, plate_id)
    _assignment_panel(ctx, grid, plate_id, selected, wells_here, universe)


def _well_details(plate_samples: pd.DataFrame, grid: pd.DataFrame, well: str | None, ctx: Ctx, plate_id: str, plate_label: str) -> None:
    """Right-hand panel: everything known about the selected well and its imaging positions."""
    if not well:
        st.info("This plate has no imaged wells.")
        return
    g = grid[grid["well_id"] == well].iloc[0]
    st.markdown(f"### Well {well}")
    status = g["mapping_status"]
    kind = "ok" if status in CONFIRMED else ("bad" if status == "conflict" else "warn")
    st.markdown(chip(STATUS_TEXT.get(status, status), kind) + (chip("missing file", "bad") if g["missing_files"] else "") +
                (chip(f"{g['n_flagged']} flagged", "warn") if g["n_flagged"] else ""), unsafe_allow_html=True)
    info = {"Plate": f"{plate_label}  (`{plate_id}`)", "Well ID": well,
            "Donor ID": g["donor_id"] if status in CONFIRMED else f"— not confirmed ({g['donor_id'] or 'no donor'})",
            "Mapping status": STATUS_TEXT.get(status, status), "Evidence": g["evidence"] or "—",
            "Candidate donors": g["candidates"] or "—", "Imaging positions": int(g["n_pos"]),
            "MOR files": int(g["n_mor"]), "TRACKING files": int(g["n_trk"]), "Frames": g["frames"] or "—", "Acquisition date(s)": g["dates"] or "—"}
    st.table(pd.Series(info, name="value").astype(str))
    pos = plate_samples[plate_samples["well_id"] == well].copy()
    pos["MOR"] = np.where(pos["morphology_file"].notna(), "✓", "✗ missing")
    pos["TRACKING"] = np.where(pos["tracking_file"].notna(), "✓", "✗ missing")
    pos["acquired"] = pos["acquisition_start"].astype(str).str[:16]
    pos["position"] = pos["position_id"]
    cols = ["position", "sample_id", "MOR", "TRACKING", "acquired", "n_frames", "label_status", "analysis_eligible", "eligibility_reason"]
    st.dataframe(pos[cols].sort_values("position"), hide_index=True, width="stretch")
    bad = pos[pos["label_detail"].fillna("") != ""]
    for r in bad.itertuples():
        st.warning(f"{r.sample_id}: {r.label_detail}")


def _donor_panel(ctx: Ctx, s: pd.DataFrame, plate_id: str) -> None:
    """Donor summary: wells, positions and files per donor (this plate and all plates)."""
    st.markdown("### Donor summary")
    scope = st.radio("Scope", ["This plate", "All plates"], horizontal=True, key="pm_scope")
    d = donor_summary(s[s["plate_id"] == plate_id] if scope == "This plate" else s)
    if d.empty:
        st.info("No data.")
        return
    colours = donor_colour_map(_donor_universe(ctx))
    d.insert(0, "colour", [colours.get(x, UNRESOLVED_COLOUR) for x in d["donor_id"]])
    st.dataframe(d, hide_index=True, width="stretch", column_config={"colour": st.column_config.TextColumn("●", width="small")})
    st.caption("'Unresolved' / 'Unassigned' rows are wells without a known donor, not donors. "
               "Wells are counted per plate: the same donor on two plates has wells on both.")
    st.download_button("Donor summary (CSV)", csv_bytes(d), "donor_summary.csv", "text/csv")


def _assignment_panel(ctx: Ctx, grid: pd.DataFrame, plate_id: str, selected: str | None, wells_here: list[str], universe: list[str]) -> None:
    """Form for confirming a donor for one or more wells, with mandatory evidence, history and undo."""
    st.markdown("### Assign wells to a confirmed donor")
    st.markdown('<div class="pc-note">Assignments are recorded with your evidence text, persisted to <code>metadata/donor_map.csv</code>, '
                'and every change (including corrections) is kept in <code>donor_map_history.csv</code>. PhaseCast never assigns '
                'donors by age, sex, morphology, motility or endpoint values.</div>', unsafe_allow_html=True)
    sel = grid[grid["well_id"] == selected].iloc[0] if selected and (grid["well_id"] == selected).any() else None
    cands = [c.strip() for c in (sel["candidates"] if sel is not None else "").split(",") if c.strip()]
    ep_ids = set(ctx.full.endpoints["donor_id"]) if len(ctx.full.endpoints) else set()
    options = list(dict.fromkeys(cands + [d for d in universe if d in ep_ids] + [d for d in universe if d not in ep_ids]))
    with st.form(f"assign_{plate_id}", clear_on_submit=False):
        wells_sel = st.multiselect("Wells to assign (this plate)", wells_here, default=[selected] if selected else [])
        all_unres = st.checkbox("Also include every UNRESOLVED well on this plate")
        donor = st.selectbox("Donor", options + ["(type a new donor id…)"], index=0 if options else None,
                             help="Candidates from the folder name are listed first. Donors that exist in the endpoint workbook are listed next.")
        new_donor = st.text_input("…or new donor id (only if the donor is not listed)")
        evidence = st.text_area("Evidence (required) - how was this donor established?",
                                placeholder="e.g. 'Plate layout sheet from Jonny, row A = 25F' or 'Confirmed by Prof. Day by email, 2026-xx-xx'")
        c = st.columns(2)
        go_assign = c[0].form_submit_button("Save assignment", type="primary")
        go_unres = c[1].form_submit_button("Mark as unresolved")
        go_clear = c[1].form_submit_button("Remove manual decision")
    if go_assign or go_unres or go_clear:
        targets = list(wells_sel)
        if all_unres:
            targets += [r.well_id for r in grid.itertuples() if r.mapping_status == "unresolved"]
        targets = sorted(set(targets))
        chosen = new_donor.strip() if (donor == "(type a new donor id…)" or new_donor.strip()) else donor
        if not targets:
            st.error("Select at least one well.")
        elif not evidence.strip():
            st.error("Evidence is required so that the assignment can be audited later.")
        elif go_assign and not chosen:
            st.error("Choose or type a donor id.")
        else:
            if go_clear:
                n = clear_donor_assignments(ctx.meta_dir, plate_id, targets, evidence)
            elif go_unres:
                n = save_donor_assignments(ctx.meta_dir, plate_id, targets, "", evidence, status="unresolved")
            else:
                n = save_donor_assignments(ctx.meta_dir, plate_id, targets, chosen, evidence)
            reload_data()
            st.success(f"Saved {n} well(s).")
            st.rerun()
    hist_path = ctx.meta_dir / "donor_map_history.csv"
    if hist_path.exists():
        h = pd.read_csv(hist_path, dtype=str, keep_default_na=False)
        h = h[h["plate_id"] == plate_id]
        with st.expander(f"Assignment history for this plate ({len(h)})"):
            st.dataframe(h.iloc[::-1], hide_index=True, width="stretch")


# ================================================================================================
# Dataset Inventory
# ================================================================================================
def dataset_inventory(ctx: Ctx) -> None:
    """PAGE: Dataset Inventory - every file, folder, position and donor that was discovered."""
    header("Dataset Inventory", "Everything PhaseCast found on disk and how it was classified (originals are never modified).")
    if not need_data(ctx):
        return
    inv, s = ctx.full.inventory, ctx.full.samples
    s = s[s["status"].eq("ok")] if "status" in s else s
    c = st.columns(6)
    c[0].metric("Files discovered", len(inv))
    c[1].metric("MOR files", int((inv["kind"] == "morphology").sum()))
    c[2].metric("TRACKING files", int((inv["kind"] == "tracking").sum()))
    c[3].metric("Endpoint workbooks", int((inv["kind"] == "endpoints").sum()))
    c[4].metric("Positions (pairs)", len(s))
    c[5].metric("Size on disk", f"{inv['size_mb'].sum():.0f} MB")
    tabs = st.tabs(["Positions", "Files", "Folders", "Donors"])
    with tabs[0]:
        cols = ["sample_id", "plate_label", "well_id", "position_id", "donor_id", "mapping_status", "morphology_file", "tracking_file", "n_frames",
                "n_cell_obs", "n_tracks", "acquisition_start", "analysis_eligible", "eligibility_reason", "label_detail", "qualifiers", "experiment_label"]
        t = s[[x for x in cols if x in s]].copy()
        f1, f2, f3 = st.columns(3)
        pl = f1.multiselect("Plate", sorted(t["plate_label"].unique()))
        dn = f2.multiselect("Donor", sorted(t["donor_id"].unique()))
        el = f3.selectbox("Eligibility", ["all", "eligible only", "held out only"])
        if pl:
            t = t[t["plate_label"].isin(pl)]
        if dn:
            t = t[t["donor_id"].isin(dn)]
        if el != "all":
            t = t[t["analysis_eligible"] == (el == "eligible only")]
        q = st.text_input("Search positions", "")
        if q:
            t = t[t.astype(str).apply(lambda r: r.str.contains(q, case=False, regex=False).any(), axis=1)]
        st.dataframe(t, hide_index=True, width="stretch")
        st.download_button("Positions (CSV)", csv_bytes(t), "positions.csv", "text/csv")
    with tabs[1]:
        k = st.multiselect("File kind", sorted(inv["kind"].unique()), default=sorted(inv["kind"].unique()))
        only_notes = st.checkbox("Only files with notes")
        v = inv[inv["kind"].isin(k)]
        if only_notes:
            v = v[v["notes"] != ""]
        st.dataframe(v, hide_index=True, width="stretch")
        st.download_button("File inventory (CSV)", csv_bytes(v), "file_inventory.csv", "text/csv")
    with tabs[2]:
        f = inv.assign(donor_labels=inv["folder"].map(lambda d: ", ".join(folder_donor_labels(d)[0]) or "—"))
        roll = f.groupby("folder").agg(files=("rel_path", "size"), mor=("kind", lambda x: int((x == "morphology").sum())),
                                       tracking=("kind", lambda x: int((x == "tracking").sum())), unpaired=("sample_id", lambda x: int((x == "").sum())),
                                       donor_labels_in_folder_name=("donor_labels", "first"), size_mb=("size_mb", "sum")).reset_index()
        st.dataframe(roll, hide_index=True, width="stretch")
        st.caption("Donor labels are read from folder names matching '<age><M|F>' (e.g. 16M). A folder with several labels is ambiguous at the well level.")
    with tabs[3]:
        st.dataframe(donor_summary(s), hide_index=True, width="stretch")


# ================================================================================================
# Endpoint Data Preview
# ================================================================================================
def endpoint_preview(ctx: Ctx) -> None:
    """PAGE: Endpoint Data Preview - the donor-level biological endpoints and how they link to imaging donors."""
    header("Endpoint Data Preview", "Fusion index, desmin and acetylcholinesterase per donor, with provenance. Preview only - nothing is correlated here.")
    ep = ctx.full.endpoints
    if ep.empty:
        st.info("No endpoint workbook was found in the data directory (a workbook whose first header cell is 'Donor'). Add it and rescan.")
        return
    cols = [c for c in ep.columns if c not in ("donor_label_original", "endpoint_source")]
    st.markdown(chip(f"{len(ep)} donors · one row each", "ok") + chip(f"source: {', '.join(sorted(set(ep['endpoint_source'])))}", "info"), unsafe_allow_html=True)
    st.dataframe(ep[cols], hide_index=True, width="stretch")
    st.download_button("Endpoint table (CSV)", csv_bytes(ep[cols]), "endpoints.csv", "text/csv")
    st.markdown("#### Columns, units and provenance")
    st.dataframe(ctx.full.endpoint_columns, hide_index=True, width="stretch")
    st.markdown("#### Issues found while reading the workbook")
    iss = ctx.full.endpoint_issues
    if len(iss):
        st.dataframe(iss[["severity", "check", "message"]], hide_index=True, width="stretch")
    st.markdown("#### Distribution across donors")
    num = [c for c in ep.columns if c.endswith(("_pct", "_u_ml"))]
    if num:
        y = st.selectbox("Endpoint", num, format_func=lambda c: {r.internal_name: r.original_name for r in ctx.full.endpoint_columns.itertuples()}.get(c, c))
        d = ep.sort_values(y)
        colours = donor_colour_map(_donor_universe(ctx))
        fig = go.Figure(go.Bar(x=d["donor_id"], y=d[y], marker_color=[colours.get(x, PALETTE[0]) for x in d["donor_id"]],
                               hovertemplate="%{x}<br>%{y:.4g}<extra></extra>"))
        style(fig, f"{y} by donor (n = {len(d)}, one value per donor)", "Donor", y, 400)
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
        st.caption("Each donor contributes exactly one value; with n = 12 donors any later analysis has very low statistical power.")
    st.markdown("#### Link between imaging donors and endpoint rows")
    aliases = load_aliases(ctx.meta_dir / "donor_aliases.csv")
    imaging = [d for d in ctx.full.samples["donor_id"].unique() if d not in (UNASSIGNED, UNRESOLVED)]
    link = link_endpoints_to_donors(ep, imaging, aliases)
    st.dataframe(link, hide_index=True, width="stretch")
    missing_img = sorted(set(ep["donor_id"]) - set(link["endpoint_donor_id"].dropna()))
    if missing_img:
        st.markdown(f'<div class="pc-note">Endpoint donors with <b>no confirmed imaging yet</b>: {", ".join(missing_img)}. '
                    'This is expected for donors whose wells are still unresolved.</div>', unsafe_allow_html=True)
    st.caption("Linking is by exact donor text (trimmed, upper-case) or by an explicit alias in metadata/donor_aliases.csv (columns: alias, donor_id). "
               "No approximate matching and no matching by endpoint values is ever done.")
    with st.expander("Donor aliases (explicit only)"):
        path = ctx.meta_dir / "donor_aliases.csv"
        cur = pd.read_csv(path, dtype=str, keep_default_na=False) if path.exists() else pd.DataFrame({"alias": [], "donor_id": [], "note": []})
        if "note" not in cur:
            cur["note"] = ""
        out = st.data_editor(cur[["alias", "donor_id", "note"]], num_rows="dynamic", hide_index=True, width="stretch", key="alias_editor")
        if st.button("Save aliases"):
            ctx.meta_dir.mkdir(parents=True, exist_ok=True)
            out.to_csv(path, index=False)
            reload_data()
            st.rerun()


# ================================================================================================
# Data Quality and Mapping
# ================================================================================================
def data_quality_mapping(ctx: Ctx) -> None:
    """PAGE: Data Quality and Mapping - what is held out of analysis and why, and how to resolve it."""
    header("Data Quality and Mapping", "File-pair problems, label conflicts, duplicates and unresolved wells - with a place to record decisions.")
    if not need_data(ctx):
        return
    full, s = ctx.full, _ok_samples(ctx)
    held = s[~s["analysis_eligible"]]
    c = st.columns(5)
    c[0].metric("Positions", len(s))
    c[1].metric("Eligible for analysis", int(s["analysis_eligible"].sum()))
    c[2].metric("Held out", len(held))
    c[3].metric("Label conflicts", int((s["label_status"] == "conflict").sum()))
    c[4].metric("Unresolved wells", int((full.wells["mapping_status"] == "unresolved").sum()))
    tabs = st.tabs(["Held-out positions", "Label conflicts", "File pairing", "Well mapping", "Resolution log"])
    with tabs[0]:
        if held.empty:
            st.success("Every imported position is eligible.")
        else:
            reasons = held["eligibility_reason"].str.split("; ").explode().value_counts().rename_axis("reason").reset_index(name="positions")
            st.dataframe(reasons, hide_index=True, width="stretch")
            st.dataframe(held[["sample_id", "plate_label", "well_id", "position_id", "donor_id", "mapping_status", "eligibility_reason", "morphology_file", "tracking_file"]],
                         hide_index=True, width="stretch")
            st.download_button("Held-out positions (CSV)", csv_bytes(held), "held_out_positions.csv", "text/csv")
    with tabs[1]:
        conf = s[(s["label_status"] == "conflict") | s["flags"].fillna("").str.contains("duplicate|experiment_conflict")]
        st.write("A position has three labels: the **file name** (typed by a person), the **MOR file's** well/position and the **TRACKING file's** "
                 "well/position (both written by the microscope software). Any disagreement is listed here.")
        if conf.empty:
            st.success("No label conflicts.")
        else:
            st.dataframe(conf[["sample_id", "label_detail", "flags", "mor_well", "mor_position", "trk_well", "trk_position",
                               "file_name_well", "file_name_position", "resolution_action"]], hide_index=True, width="stretch")
        _resolution_form(ctx, s)
    with tabs[2]:
        p = ctx.processed_dir / "discovery.json"
        probs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        pr = pd.DataFrame(probs)
        if len(pr):
            sev = st.multiselect("Severity", ["error", "warning", "info"], default=["error", "warning"])
            st.dataframe(pr[pr["severity"].isin(sev)], hide_index=True, width="stretch")
            st.caption("'cross_folder_candidate': a MOR file and a TRACKING file with the same name sit in different folders. They are never paired "
                       "automatically; decide with the experimenter whether they belong together.")
        else:
            st.success("No file-pairing problems.")
        dup = s[s["duplicate_of"] != ""] if "duplicate_of" in s else pd.DataFrame()
        st.write(f"Byte-identical duplicate imports excluded: **{len(dup)}**")
    with tabs[3]:
        w = full.wells
        st.dataframe(w.sort_values(["plate_id", "well_id"]), hide_index=True, width="stretch")
        st.caption("Status: confirmed_manual = decided in the Plate Mapper; confirmed_folder = the folder name carries one donor label; "
                   "unresolved = several candidate donors; conflict = folders disagree; unassigned = no information.")
    with tabs[4]:
        r = load_resolutions(ctx.meta_dir)
        st.dataframe(r, hide_index=True, width="stretch")
        h = ctx.meta_dir / "donor_map_history.csv"
        if h.exists():
            st.write("Donor assignment history")
            st.dataframe(pd.read_csv(h, dtype=str, keep_default_na=False).iloc[::-1], hide_index=True, width="stretch")


def _resolution_form(ctx: Ctx, s: pd.DataFrame) -> None:
    """Record how a flagged position is to be treated (always with evidence)."""
    flagged = s[(~s["analysis_eligible"]) | (s["resolution_action"] != "")]
    flagged = flagged[flagged["flags"].fillna("") != ""]
    if flagged.empty:
        return
    st.markdown("##### Record a decision for a flagged position")
    with st.form("resolve_form"):
        sid = st.selectbox("Position", list(flagged["sample_id"]))
        action = st.selectbox("Decision", RESOLUTION_ACTIONS, format_func={
            "exclude": "Exclude this position from analysis",
            "use_mor_label": "Trust the MOR file's well/position",
            "use_trk_label": "Trust the TRACKING file's well/position",
            "use_filename_label": "Trust the file name's well/position",
            "include_as_is": "Include as is (accept the flag)"}.get)
        ev = st.text_area("Evidence (required)")
        if st.form_submit_button("Save decision", type="primary"):
            try:
                save_resolution(ctx.meta_dir, sid, action, ev)
                reload_data()
                st.rerun()
            except ValueError as e:
                st.error(str(e))
