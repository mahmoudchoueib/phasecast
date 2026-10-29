"""Tests for PhaseCast chunk 1: file pairing, plate/donor mapping, endpoint joins, duplicate detection, data integrity.

Most tests use small synthetic tables so they run in milliseconds and do not depend on the researcher's own
(editable) metadata files; the "real data" tests load processed/ with an EMPTY metadata folder so results are deterministic.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

from hmx.discovery import classify_by_name, discover_files, pair_files
from hmx.endpoints import (link_endpoints_to_donors, load_aliases, normalise_donor_id, read_endpoint_workbook, resolve_donor)
from hmx.features import join_endpoints
from hmx.mapping import (UNASSIGNED, UNRESOLVED, apply_mapping, assess_samples, clear_donor_assignments, donor_summary,
                         effective_well_table, folder_donor_labels, load_donor_map, load_resolutions, parse_file_key,
                         parse_group_label, plate_id_from_experiment, save_donor_assignments, save_resolution, suggest_plate_format)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw_data"
SAMPLE_MOR, SAMPLE_TRK = RAW / "16M" / "A1P6-MOR.xlsx", RAW / "16M" / "A1P6-TRACKING.xlsx"
needs_raw = pytest.mark.skipif(not SAMPLE_MOR.exists(), reason="raw_data not present")


# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------
def make_samples(rows: list[dict]) -> pd.DataFrame:
    """Minimal 'samples' table with the columns assess_samples/apply_mapping read."""
    base = dict(rel_dir="", key=None, mor_group=None, trk_group=None, mor_experiment="EXP A", trk_experiment="EXP A",
                morphology_file="m.xlsx", tracking_file="t.xlsx", n_frames=145, acquisition_start="2026-07-21T15:00:00", status="ok")
    return pd.DataFrame([{**base, **r} for r in rows])


def pipeline(samples: pd.DataFrame, meta: Path, aliases=None):
    s = assess_samples(samples, load_resolutions(meta))
    wells = effective_well_table(s, load_donor_map(meta), aliases or {})
    return apply_mapping(s, wells), wells


G = lambda w, p: f"Well {w} - Position {p}"   # noqa: E731


# ------------------------------------------------------------------------------------------------
# file-name pairing
# ------------------------------------------------------------------------------------------------
def test_name_classification_tolerates_real_irregular_names():
    assert classify_by_name("A1P6-MOR") == ("morphology", "A1P6", "")
    assert classify_by_name("A2P1-MOR(BLUR") == ("morphology", "A2P1", "(BLUR")
    assert classify_by_name("A2P1-TRACKING(BLUR") == ("tracking", "A2P1", "(BLUR")
    assert classify_by_name("C2P4-TRACKING#") == ("tracking", "C2P4", "#")
    assert classify_by_name("B1P1")[0] == "unknown"          # no suffix: decided from worksheets instead


@needs_raw
def test_pairing_handles_irregular_names_and_never_pairs_across_folders(tmp_path):
    (tmp_path / "f1").mkdir()
    (tmp_path / "f2").mkdir()
    cp = lambda src, dst: shutil.copy(src, tmp_path / dst)   # noqa: E731
    cp(SAMPLE_MOR, "f1/A2P1-MOR(BLUR.xlsx"); cp(SAMPLE_TRK, "f1/A2P1-TRACKING(BLUR.xlsx")      # qualifier on both
    cp(SAMPLE_MOR, "f1/B1P1.xlsx"); cp(SAMPLE_TRK, "f1/B1P1-TRACKING.xlsx")                       # MOR without suffix
    cp(SAMPLE_MOR, "f1/D2P6-MOR.xlsx"); cp(SAMPLE_TRK, "f2/D2P6-TRACKING.xlsx")                   # partners in different folders
    files = discover_files(tmp_path)
    pairs, problems = pair_files(files)
    ids = {p.sample_id: p for p in pairs}
    assert ids["f1/A2P1"].morphology and ids["f1/A2P1"].tracking
    assert ids["f1/B1P1"].morphology and ids["f1/B1P1"].tracking
    assert ids["f1/D2P6"].tracking is None and ids["f2/D2P6"].morphology is None          # NOT auto-paired
    assert any(p["check"] == "cross_folder_candidate" for p in problems)
    assert any(f.qualifier == "(BLUR" for f in files)


@needs_raw
def test_endpoint_workbook_is_recognised_by_structure(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Donor", "FI (%)"]); ws.append(["1M", 5.0])
    wb.save(tmp_path / "whatever name.xlsx")
    kinds = {f.path.name: f.kind for f in discover_files(tmp_path)}
    assert kinds["whatever name.xlsx"] == "endpoints"


# ------------------------------------------------------------------------------------------------
# folder labels / plate identity / small parsers
# ------------------------------------------------------------------------------------------------
def test_folder_donor_labels():
    assert folder_donor_labels("raw_data/16M") == (["16M"], "16M")
    assert folder_donor_labels("raw_data/25F/DUPLICATES")[0] == ["25F"]
    labels, comp = folder_donor_labels("raw_data/PLATE2-25F_40M_34F_20M")
    assert labels == ["25F", "40M", "34F", "20M"] and comp == "PLATE2-25F_40M_34F_20M"
    assert folder_donor_labels("raw_data/PLATE2")[0] == []


def test_label_parsers_and_plate_geometry_suggestion():
    assert parse_group_label("Well A1 - Position 6") == ("A1", "6")
    assert parse_file_key("B3P2") == ("B3", "2") and parse_file_key("weird") == (None, None)
    assert suggest_plate_format(["A1", "D6", "B3"]) == (4, 6, 24)       # smallest standard plate containing D6
    assert suggest_plate_format(["A1", "A2"])[2] == 6
    # same well name, different experiments -> different plate ids (no collision)
    assert plate_id_from_experiment("Cell Morphology---Jonny PLATE3") != plate_id_from_experiment("Cell Morphology-plate4-Jonny")


# ------------------------------------------------------------------------------------------------
# donor mapping
# ------------------------------------------------------------------------------------------------
def test_single_folder_label_is_confirmed_and_eligible(tmp_path):
    s = make_samples([dict(sample_id="16M/A1P2", key="A1P2", rel_dir="raw/16M", mor_group=G("A1", 2), trk_group=G("A1", 2))])
    out, wells = pipeline(s, tmp_path)
    r = out.iloc[0]
    assert (r.donor_id, r.mapping_status, r.analysis_eligible) == ("16M", "confirmed_folder", True)


def test_multi_donor_folder_is_unresolved_and_excluded_by_default(tmp_path):
    s = make_samples([dict(sample_id="P2/A1P1", key="A1P1", rel_dir="raw/PLATE2-25F_40M_34F_20M", mor_group=G("A1", 1), trk_group=G("A1", 1))])
    out, wells = pipeline(s, tmp_path)
    r = out.iloc[0]
    assert r.donor_id == UNRESOLVED and r.mapping_status == "unresolved" and not r.analysis_eligible
    assert set(wells.iloc[0].candidates.split(", ")) == {"25F", "40M", "34F", "20M"}      # candidates shown, none assigned


def test_manual_assignment_needs_evidence_persists_and_can_be_corrected(tmp_path):
    s = make_samples([dict(sample_id="P2/A1P1", key="A1P1", rel_dir="raw/PLATE2-25F_40M_34F_20M", mor_group=G("A1", 1), trk_group=G("A1", 1))])
    plate = assess_samples(s).iloc[0].plate_id
    with pytest.raises(ValueError):
        save_donor_assignments(tmp_path, plate, ["A1"], "25F", evidence="  ")                  # evidence is mandatory
    save_donor_assignments(tmp_path, plate, ["A1"], "25F", evidence="plate layout sheet")
    out, _ = pipeline(s, tmp_path)
    assert (out.iloc[0].donor_id, out.iloc[0].mapping_status, bool(out.iloc[0].analysis_eligible)) == ("25F", "confirmed_manual", True)
    save_donor_assignments(tmp_path, plate, ["A1"], "40M", evidence="correction after checking with supervisor")
    out, _ = pipeline(s, tmp_path)
    assert out.iloc[0].donor_id == "40M"
    hist = pd.read_csv(tmp_path / "donor_map_history.csv", dtype=str, keep_default_na=False)
    assert list(hist["new_donor_id"]) == ["25F", "40M"] and hist.iloc[1]["old_donor_id"] == "25F"      # audit trail
    clear_donor_assignments(tmp_path, plate, ["A1"], evidence="undo")
    out, _ = pipeline(s, tmp_path)
    assert out.iloc[0].mapping_status == "unresolved"                                          # falls back, nothing guessed


def test_two_folders_naming_different_donors_for_one_well_is_a_conflict(tmp_path):
    s = make_samples([dict(sample_id="32M/D2P1", key="D2P1", rel_dir="raw/32M", mor_group=G("D2", 1), trk_group=G("D2", 1)),
                      dict(sample_id="35M/D2P6", key="D2P6", rel_dir="raw/35M", mor_group=None, mor_experiment=None, morphology_file=None,
                           trk_group=G("D2", 6))])
    out, wells = pipeline(s, tmp_path)
    assert wells.iloc[0].mapping_status == "conflict" and not out.analysis_eligible.any()


def test_same_well_on_different_plates_gets_independent_donors(tmp_path):
    s = make_samples([dict(sample_id="16M/B3P1", key="B3P1", rel_dir="raw/16M", mor_group=G("B3", 1), trk_group=G("B3", 1), mor_experiment="PLATE3", trk_experiment="PLATE3"),
                      dict(sample_id="17F/B3P1", key="B3P1", rel_dir="raw/17F", mor_group=G("B3", 1), trk_group=G("B3", 1), mor_experiment="PLATE4", trk_experiment="PLATE4")])
    out, wells = pipeline(s, tmp_path)
    assert dict(zip(out.sample_id, out.donor_id)) == {"16M/B3P1": "16M", "17F/B3P1": "17F"} and len(wells) == 2


def test_label_conflict_blocks_analysis_until_resolved(tmp_path):
    s = make_samples([dict(sample_id="17F/C3P6", key="C3P6", rel_dir="raw/17F", mor_group=G("C3", 6), trk_group=G("D3", 1))])
    out, _ = pipeline(s, tmp_path)
    assert out.iloc[0].label_status == "conflict" and not out.iloc[0].analysis_eligible
    assert "TRACKING file says D3" in out.iloc[0].label_detail
    with pytest.raises(ValueError):
        save_resolution(tmp_path, "17F/C3P6", "use_mor_label", "")                            # evidence mandatory
    save_resolution(tmp_path, "17F/C3P6", "use_mor_label", "MOR file matches file name; tracking export mislabelled")
    out, _ = pipeline(s, tmp_path)
    assert out.iloc[0].label_status == "resolved" and out.iloc[0].well_id == "C3" and out.iloc[0].analysis_eligible


def test_duplicate_acquisition_flagged_then_resolved_by_exclusion(tmp_path):
    base = dict(rel_dir="raw/PLATE2-25F_40M_34F_20M", mor_group=G("C5", 1), trk_group=G("C5", 1))
    s = make_samples([dict(sample_id="P/C4P3", key="C4P3", **base), dict(sample_id="P/C5P1", key="C5P1", **base)])
    out, _ = pipeline(s, tmp_path)
    assert out["flags"].str.contains("duplicate_acquisition").all()
    save_resolution(tmp_path, "P/C4P3", "exclude", "same acquisition exported twice")
    s2 = assess_samples(s, load_resolutions(tmp_path))
    assert not s2.set_index("sample_id").loc["P/C5P1", "flags"].count("duplicate_acquisition")


def test_duplicates_folder_is_flagged_not_silently_included(tmp_path):
    s = make_samples([dict(sample_id="25F/DUPLICATES/B3P2", key="B3P2", rel_dir="raw/25F/DUPLICATES", mor_group=G("B3", 2), trk_group=G("B3", 2))])
    out, _ = pipeline(s, tmp_path)
    assert out.iloc[0].donor_id == "25F" and not out.iloc[0].analysis_eligible and "DUPLICATES" in out.iloc[0].eligibility_reason
    save_resolution(tmp_path, "25F/DUPLICATES/B3P2", "include_as_is", "confirmed with experimenter: distinct acquisitions")
    out, _ = pipeline(s, tmp_path)
    assert out.iloc[0].analysis_eligible


def test_donor_summary_counts_wells_positions_and_files(tmp_path):
    rows = [dict(sample_id=f"16M/A1P{i}", key=f"A1P{i}", rel_dir="raw/16M", mor_group=G("A1", i), trk_group=G("A1", i)) for i in (1, 2)]
    rows += [dict(sample_id="16M/A2P1", key="A2P1", rel_dir="raw/16M", mor_group=G("A2", 1), trk_group=None, tracking_file=None)]
    out, _ = pipeline(make_samples(rows), tmp_path)
    d = donor_summary(out).set_index("donor_id").loc["16M"]
    assert (d.wells, d.imaging_positions, d.MOR_files, d.TRACKING_files) == (2, 3, 3, 2)


# ------------------------------------------------------------------------------------------------
# endpoints
# ------------------------------------------------------------------------------------------------
def make_endpoint_workbook(path: Path, donors=("16M", "17F", "25F"), duplicate=False) -> Path:
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Donor", "FI (%)", "Medium Mean (U/mL)", "Lysate Mean (U/mL)", "Desmin %", None])
    for i, d in enumerate(donors):
        ws.append([d, 10.0 + i, -0.001 if i == 2 else 0.002, 0.003, 50.0 + i, "44.6?" if i == 2 else None])
    if duplicate:
        ws.append([donors[0], 1, 1, 1, 1, None])
    wb.save(path)
    return path


def test_endpoint_workbook_one_row_per_donor_units_flags_and_verbatim_notes(tmp_path):
    res = read_endpoint_workbook(make_endpoint_workbook(tmp_path / "e.xlsx"))
    t = res.table
    assert list(t.donor_id) == ["16M", "17F", "25F"] and t.donor_id.is_unique
    assert t.loc[t.donor_id == "25F", "ache_medium_u_ml"].iloc[0] == pytest.approx(-0.001)              # negative kept unchanged
    assert t.loc[t.donor_id == "25F", "row_note"].iloc[0] == "44.6?" and t.loc[t.donor_id == "16M", "row_note"].iloc[0] == ""
    cols = res.columns.set_index("internal_name")
    assert cols.loc["fi_pct", "original_name"] == "FI (%)" and cols.loc["ache_lysate_u_ml", "unit"] == "U/mL"
    assert cols.loc["ache_medium_u_ml", "interpretation_status"] == "requires confirmation"            # 'Medium' vs 'membrane'
    checks = {i["check"] for i in res.issues}
    assert {"endpoint_negative_values", "endpoint_row_note", "endpoint_one_row_per_donor"} <= checks


def test_endpoint_duplicate_donor_is_reported(tmp_path):
    res = read_endpoint_workbook(make_endpoint_workbook(tmp_path / "e.xlsx", duplicate=True))
    assert any(i["check"] == "endpoint_duplicate_donor" and i["severity"] == "error" for i in res.issues)


def test_endpoint_join_is_exact_alias_only_and_never_fuzzy(tmp_path):
    ep = read_endpoint_workbook(make_endpoint_workbook(tmp_path / "e.xlsx")).table
    imaging = ["16M", "17F", "17f_new", "25 F", UNRESOLVED]
    link = link_endpoints_to_donors(ep, imaging, {}).set_index("imaging_donor_id")
    assert link.loc["16M", "matched_by"] == "exact"
    assert link.loc["25 F", "matched_by"] == "exact"                       # whitespace/case normalisation only
    assert link.loc["17f_new", "matched_by"] == "no endpoint row"          # similar-looking name is NOT matched
    (tmp_path / "donor_aliases.csv").write_text("alias,donor_id,note\n17f_new,17F,renamed in 2nd experiment\n", encoding="utf-8")
    al = load_aliases(tmp_path / "donor_aliases.csv")
    assert resolve_donor("17F_NEW", al) == "17F"
    assert link_endpoints_to_donors(ep, ["17f_new"], al).iloc[0].matched_by == "alias"
    assert normalise_donor_id(" 25 f ") == "25F"


def test_endpoint_join_leaves_unresolved_donors_empty(tmp_path):
    ep = read_endpoint_workbook(make_endpoint_workbook(tmp_path / "e.xlsx")).table
    table = pd.DataFrame({"donor_id": ["16M", UNRESOLVED, "99X"], "cutoff_h": [24, 24, 24]})
    out = join_endpoints(table, ep).set_index("donor_id")
    assert out.loc["16M", "fi_pct"] == 10.0
    assert np.isnan(out.loc[UNRESOLVED, "fi_pct"]) and np.isnan(out.loc["99X", "fi_pct"])             # nothing invented
    assert join_endpoints(table, pd.DataFrame()).fusion_index.isna().all()


@needs_raw
def test_real_endpoint_workbook_structure():
    f = RAW / "Combined Data Jonny Project.xlsx"
    if not f.exists():
        pytest.skip("endpoint workbook not present")
    res = read_endpoint_workbook(f)
    assert len(res.table) == 12 and res.table.donor_id.is_unique                                 # one row per donor, verified
    assert {"16M", "17F", "20F", "20M", "22M", "25F", "25M", "31F", "32M", "34F", "35M", "40M"} == set(res.table.donor_id)
    assert res.table.fi_pct.notna().all() and res.table.desmin_pct.notna().all()


# ------------------------------------------------------------------------------------------------
# data integrity on the real, processed data (empty metadata folder => deterministic)
# ------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real(tmp_path_factory):
    from hmx.store import load_dataset
    proc = ROOT / "processed"
    if not list((proc / "samples").glob("*/meta.json")):
        pytest.skip("processed data not present - run: python -m hmx.cli ingest")
    return load_dataset(proc, None, meta_dir=tmp_path_factory.mktemp("emptymeta"))


def test_real_hierarchy_columns_and_no_cross_sample_mixing(real):
    for name in ("frames", "cells", "track_obs", "track_summary"):
        df = getattr(real, name)
        assert {"sample_id", "donor_id", "plate_id", "well_id", "position_id"} <= set(df.columns), name
        assert df.groupby("sample_id", observed=True)["donor_id"].nunique().max() == 1             # a position has exactly one donor label
    assert real.cells.duplicated(["sample_id", "frame_no", "cell_id"]).sum() == 0
    per = real.cells.groupby("sample_id", observed=True).size()
    exp = real.frames.groupby("sample_id", observed=True)["n_cells"].sum()
    exp = exp[exp.index.isin(per.index)]                          # tracking-only positions have no cell rows\n    assert (per.reindex(exp.index) == exp).all()                  # frame counts reconcile with cell rows


def test_real_unresolved_wells_are_excluded_from_default_analysis(real):
    s = real.samples
    plate2 = s[s.rel_dir.str.contains("PLATE2")]
    assert len(plate2) == 40 and (plate2.donor_id == UNRESOLVED).all() and not plate2.analysis_eligible.any()
    el = s[s.analysis_eligible]
    assert el.mapping_status.isin(["confirmed_folder", "confirmed_manual"]).all()
    assert not el.donor_id.isin([UNRESOLVED, UNASSIGNED]).any()


def test_real_folder_donors_all_exist_in_endpoint_workbook(real):
    imaging = set(real.samples.loc[real.samples.mapping_status == "confirmed_folder", "donor_id"])
    assert imaging and imaging <= set(real.endpoints.donor_id)


def test_real_label_conflicts_and_cross_folder_pair_are_detected(real):
    s = real.samples.set_index("sample_id")
    assert s.loc["17F/C3P6", "label_status"] == "conflict" and not s.loc["17F/C3P6", "analysis_eligible"]
    assert s.loc["32M/D2P6", "morphology_file"] is not None and pd.isna(s.loc["32M/D2P6", "tracking_file"])   # misfiled TRACKING not auto-paired
    assert real.samples[real.samples.rel_dir.str.contains("DUPLICATES")].analysis_eligible.eq(False).all()




# ------------------------------------------------------------------------------------------------
# content duplicates (renamed copies of the same measurements)
# ------------------------------------------------------------------------------------------------
def test_content_duplicate_flags_both_then_exclusion_clears_the_other(tmp_path):
    s = make_samples([dict(sample_id="17F/D3P1", key="D3P1", rel_dir="raw/17F", mor_group=G("D3", 1), trk_group=G("D3", 1), sig_trk="abc", sig_mor="m1"),
                      dict(sample_id="17F/C3P6", key="C3P6", rel_dir="raw/17F", mor_group=G("C3", 6), trk_group=G("C3", 6), sig_trk="abc", sig_mor="m2")])
    out, _ = pipeline(s, tmp_path)
    assert out["flags"].str.contains("content_duplicate").all() and not out.analysis_eligible.any()
    assert "TRACKING data identical to 17F/D3P1" in out.set_index("sample_id").loc["17F/C3P6", "content_duplicate_detail"]
    save_resolution(tmp_path, "17F/C3P6", "exclude", "tracking file is a renamed copy of D3P1")
    out, _ = pipeline(s, tmp_path)
    assert out.set_index("sample_id").loc["17F/D3P1", "analysis_eligible"]            # the genuine one is released


@needs_raw
def test_real_content_duplicates_detected(real):
    s = real.samples.set_index("sample_id")
    assert "17F/D3P1" in s.loc["17F/C3P6", "content_duplicate_detail"]
    assert "25F/B4P1" in s.loc["25F/B4P2", "content_duplicate_detail"]
    assert not s.loc["17F/C3P6", "analysis_eligible"] and not s.loc["25F/B4P2", "analysis_eligible"]
