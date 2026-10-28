"""Automated checks on the real sample files and on the multi-file behaviour. Run: pytest -q"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hmx.discovery import discover_files, pair_files
from hmx.features import window_features_for_sample
from hmx.parsing import parse_morphology, parse_tracking
from hmx.store import ingest, load_dataset
from hmx.tracks import track_summary

ROOT = Path(__file__).resolve().parent.parent
MOR, TRK = ROOT / "A1P6-MOR.xlsx", ROOT / "A1P6-TRACKING.xlsx"
pytestmark = pytest.mark.skipif(not (MOR.exists() and TRK.exists()), reason="sample workbooks not present")


@pytest.fixture(scope="module")
def morph():
    return parse_morphology(MOR)


@pytest.fixture(scope="module")
def track():
    return parse_tracking(TRK)


@pytest.fixture(scope="module")
def multi(tmp_path_factory):
    """data dir with: A1P6 pair; sub/A1P6 pair (identical copy); C3P1 morphology only."""
    d = tmp_path_factory.mktemp("data")
    shutil.copy(MOR, d / "A1P6-MOR.xlsx")
    shutil.copy(TRK, d / "A1P6-TRACKING.xlsx")
    (d / "sub").mkdir()
    shutil.copy(MOR, d / "sub" / "A1P6-MOR.xlsx")
    shutil.copy(TRK, d / "sub" / "A1P6-TRACKING.xlsx")
    shutil.copy(MOR, d / "C3P1-MOR.xlsx")
    out = d.parent / (d.name + "_processed")
    rep = ingest(d, out)
    return d, out, rep


def test_both_sample_files_load(morph, track):
    assert len(morph.frames) == 145 and len(morph.cells) == 2137
    assert len(track.obs) == 1627 and track.obs["track_id"].nunique() == 191


def test_discovery_recognises_and_pairs():
    files = [f for f in discover_files(ROOT) if f.path.name.startswith("A1P6")]
    kinds = {f.path.name: f.kind for f in files}
    assert kinds == {"A1P6-MOR.xlsx": "morphology", "A1P6-TRACKING.xlsx": "tracking"}
    pairs, problems = pair_files(files)
    assert len(pairs) == 1 and pairs[0].sample_id == "A1P6" and not problems


def test_frame_counts_reconcile_with_cells(morph):
    per_frame = morph.cells.groupby("frame_no").size()
    assert (per_frame.reindex(morph.frames["frame_no"]).fillna(0).to_numpy() == morph.frames["n_cells"].to_numpy()).all()
    assert morph.frames["n_cells"].sum() == len(morph.cells) == int(morph.info["No. cells*"])


def test_timestamps_parsed_day_first(morph):
    f = morph.frames
    assert f["acquired_at"].iloc[0] == pd.Timestamp("2026-07-21 15:44:36")
    assert f["acquired_at"].iloc[-1] == pd.Timestamp("2026-07-24 15:44:28")
    assert f["elapsed_h"].iloc[0] == 0 and 71.9 < f["elapsed_h"].iloc[-1] < 72.0
    assert 29.5 < f["elapsed_s"].diff().median() / 60 < 30.5


def test_units_and_original_names_preserved(morph, track):
    prov = {(p["table"], p["original"]): p for p in morph.provenance + track.provenance}
    assert prov[("cells", "Area (µm²)")]["unit"] == "µm²" and prov[("cells", "Area (µm²)")]["internal"] == "area_um2"
    assert prov[("cells", "Optical volume (µm³)")]["unit"] == "µm³"
    assert prov[("frames", "Confluence (%)")]["unit"] == "%"
    assert prov[("track_obs", "Motility speed (µm/h)")]["unit"] == "µm/h"
    assert all(p["mapping"] == "explicit" for p in morph.provenance + track.provenance), "unexpected unmapped columns"


def test_tracking_lineage_and_general_stats(track):
    s = track_summary(track.obs)
    assert len(s) == 191 and s["family"].nunique() == 71
    assert int(s["divides"].sum()) == int(track.general["No. cell divisions"]) == 60
    assert int(s["cycle_complete"].sum()) == int(track.general["No. complete cell cycles"])
    assert abs(s["cycle_length_s"].mean() - track.general["Avg. cell cycle length"].total_seconds()) < 5


def test_multiple_pairs_remain_separated(multi):
    d, out, rep = multi
    ds = load_dataset(out, None, exclude_duplicates=False)
    assert set(ds.samples["sample_id"]) == {"A1P6", "sub/A1P6", "C3P1"}
    assert ds.cells.groupby("sample_id", observed=True).size().to_dict() == {"A1P6": 2137, "sub/A1P6": 2137, "C3P1": 2137}
    assert ds.track_summary.groupby("sample_id", observed=True).size().to_dict() == {"A1P6": 191, "sub/A1P6": 191}
    assert (ds.samples["donor_id"] == "Unassigned").all()          # donor never inferred from file names


def test_missing_tracking_file_does_not_crash(multi):
    d, out, rep = multi
    assert not rep.failed
    ds = load_dataset(out, None, exclude_duplicates=False)
    c3 = ds.samples[ds.samples["sample_id"] == "C3P1"].iloc[0]
    assert c3["n_cell_obs"] == 2137 and c3["n_tracks"] == 0
    assert any(p["check"] == "unmatched_pair" and p.get("sample_id") == "C3P1" for p in rep.problems)


def test_duplicate_import_does_not_duplicate_records(multi):
    d, out, rep1 = multi
    rep2 = ingest(d, out)                                              # second import: nothing changed
    assert not rep2.processed and len(rep2.skipped_unchanged) == 3
    ds = load_dataset(out, None, exclude_duplicates=True)              # identical copies are excluded
    assert ds.samples.set_index("sample_id")["duplicate_of"].to_dict() == {"A1P6": "", "C3P1": "", "sub/A1P6": "A1P6"}
    assert set(ds.cells["sample_id"].astype(str)) == {"A1P6", "C3P1"}          # copy contributes no rows
    assert len(ds.cells) == 2 * 2137
    assert len(ds.cells.drop_duplicates(["sample_id", "frame_no", "cell_id"])) == len(ds.cells)
    assert (ds.issues["check"] == "duplicate_import").any()


def test_corrupt_file_is_logged_not_fatal(tmp_path):
    shutil.copy(MOR, tmp_path / "A1P6-MOR.xlsx")
    (tmp_path / "BAD-MOR.xlsx").write_bytes(b"not a workbook")
    rep = ingest(tmp_path, tmp_path / "proc")
    assert "A1P6" in rep.processed
    assert any(p["check"] == "unrecognised_file" for p in rep.problems)


def test_original_files_untouched(multi):
    assert MOR.stat().st_size == 1222159 and TRK.stat().st_size == 2457105


def test_early_window_never_uses_later_observations(morph, track):
    from hmx.tracks import add_family_coordinates
    cells, frames, obs = morph.cells, morph.frames, add_family_coordinates(track.obs)
    feats, ffeats = ["area_um2", "hull_convexity"], ["area_um2", "confluence_pct"]
    T = 12.0
    base = window_features_for_sample(cells, frames, obs, T, feats, ffeats)
    assert base["window_max_elapsed_h"] <= T
    # sabotage everything after T: features must not change
    c2, f2, o2 = cells.copy(), frames.copy(), obs.copy()
    c2.loc[c2["elapsed_h"] > T, feats] = 1e6
    f2.loc[f2["elapsed_h"] > T, ["confluence_pct"]] = 1e6
    late = o2["elapsed_s"] > T * 3600
    o2.loc[late, ["rel_x_um", "rel_y_um", "motility_um", "area_um2"]] = 1e6
    o2.loc[late, "end_status"] = "Divides"
    again = window_features_for_sample(c2, f2, o2, T, feats, ffeats)
    for k, v in base.items():
        w = again[k]
        assert (v == w) or (isinstance(v, float) and np.isnan(v) and np.isnan(w)), k
    assert base["n_frames"] == int((frames["elapsed_h"] <= T).sum())


def test_early_window_cutoffs_nested():
    # more cutoff -> more observations, window max never exceeds cutoff
    m = parse_morphology(MOR)
    prev = 0
    for T in (6, 12, 24, 36, 48, 72):
        r = window_features_for_sample(m.cells, m.frames, pd.DataFrame(), T, ["area_um2"], [], traj=False)
        assert r["n_cell_obs"] >= prev and r["window_max_elapsed_h"] <= T
        prev = r["n_cell_obs"]

