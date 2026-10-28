# phasecast
AI-driven analysis of longitudinal quantitative phase imaging for early, non-destructive prediction of regenerative cell quality/potency

---

# HoloMonitor Cell Morphology and Tracking Explorer

Local Streamlit dashboard for exploratory analysis of HoloMonitor `-MOR.xlsx` (morphology) and `-TRACKING.xlsx`
(tracking) exports. Exploratory only: no machine learning, no fusion-index correlation, no biological claims.
Original Excel files are only ever **read**.

## Windows setup (PowerShell, in this folder)

```powershell
# 1. Python 3.11+ required (3.12 used in development). Create and activate a virtual environment:
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
# 2. Install dependencies
pip install -r requirements.txt
# 3. Launch
streamlit run app.py
```

(If `python` is not found, use any Python 3.11+ interpreter, e.g. the Spyder runtime at
`C:\Users\<you>\AppData\Local\spyder-6\envs\spyder-runtime\python.exe -m venv .venv`.)
In VS Code: open the folder, select the `.venv` interpreter, run the command above in the integrated terminal.
The app opens at http://localhost:8501. Click **Scan folder and import** in the sidebar the first time
(by default it searches this folder recursively).

## Adding more data (no code changes)

1. Copy `XXX-MOR.xlsx` + `XXX-TRACKING.xlsx` into the data directory (any sub-folders). Files are paired by shared name key
   (`A1P6-MOR` ↔ `A1P6-TRACKING`) **within the same folder**; recognition also checks worksheet structure.
2. Sidebar → **Scan folder and import**. Only new/changed files are processed (SHA-256 fingerprint); failures are logged and skipped.
3. Assign metadata on *Dataset Overview → Sample metadata*, or edit `manifest.csv` (see `manifest.sample.csv`):
   `sample_key, donor_id, run_id, well_id, position_id, passage, culture_date, fusion_index, notes`.
   Samples in different folders may reuse the same file name; their `sample_key` is `subfolder/A1P6`.
   `A1P6` is **not** treated as a donor id; donors stay "Unassigned" until the manifest says otherwise.
Command line equivalent: `python -m hmx.cli ingest [--force] [--only A1P6]`.

## What is implemented

| Page | Content |
|---|---|
| Demo Overview | 5 auto-generated analyses with computed observations, cautions, CSV + PNG export |
| Dataset Overview | counts, time span, missingness, warnings, searchable dataset table, manifest editor |
| Morphology Explorer | any cell/frame feature vs time; mean/median; SD/IQR band; raw/smoothed; equal-frame vs pooled weighting; CSV |
| Cell Population Distributions | histogram/box/violin, preset 0–6…0–72 h or custom windows A/B, summary stats, effect sizes, heterogeneity over time |
| Feature Relationships | scatter, Pearson/Spearman, trend line, heatmap, colour by time/donor/position/well |
| Cell Tracking Explorer | trajectories (origin / family / imaging-field frames), speed, displacement, directness, divisions, cell cycle, lineage |
| Early-Window Features | per position × cutoff (6/12/24/36/48/72 h) cell-, frame- and trajectory-level features; CSV/Parquet export |
| Quality Control & Dictionary | issues, missingness, duplicates, intervals, flagged records, import log, searchable dictionary, QC report |

Layout: `hmx/` (readers, discovery, parsing, spec = explicit feature mapping, tracks, validation, metadata, store = ingestion + Parquet,
analysis, features, viz, page_*/ui_common = dashboard), `tests/`, `processed/` (generated Parquet), `docs/data_dictionary.*` (generated).
Tests: `pytest -q` (21 tests; includes sample-file loading, reconciliation, causality of early windows, duplicates, missing files).

## Findings about the file structure (verified on the sample, encoded in the code and QC checks)

* Morphology: `Frame stats` (145 frames; frame data + software-computed per-frame averages), `All cells` (2137 cells; edge cells excluded
  by the software), `Frame info` (image scale 0.55332 µm/pxl, 1024 px, 635 nm laser). Timestamps are day-first, 1 s resolution, 30-min interval.
* Morphology `Cell ID` is a **per-frame label**, not a persistent track ID.
* Tracking: 8 feature×time matrices, 71 per-family sheets (`Cell n`, containing all descendants `Cell n-1-2…`), `Averages`, `General stats`, `Info`.
  Matrices and per-cell sheets agree exactly. `Cell feature graphs` is empty (title only).
* **Relative position** is relative to the track's own first position for founder cells but to the **parent's last position** for daughters
  (checked against centroids). The app derives lineage-continuous coordinates from this.
* Exported *Migration* = distance from that origin; *Motility* = accumulated path (for daughters it starts at the parent→daughter offset, not 0);
  *Motility speed* is a **windowed mean speed** (≈2.5 h window inferred from the data), not per-step speed. Per-step kinematics are recomputed.
* Division = `End status` "Divides"; daughters are named `<parent>-1/-2`. Cell-cycle length = daughters' first appearance − own first appearance;
  this reproduces the software's own 41 complete cycles and mean 5.84 h.
* Tracking and morphology share no identifier; observations are linked **only by exact equality of frame, Area and Optical volume** and validated
  against positions (84.6 % of tracking observations link; the rest are presumably edge cells - not confirmed). The link is opt-in in *Feature Relationships*.

## Assumptions and limitations

* Donor/run/passage/culture date/fusion index come only from the manifest. `Experiment` text ("…Jonny PLATE3") and `Group` are shown, never interpreted.
  Well/position are read from `Group` ("Well A1 - Position 6") and overridden (with a warning on conflict) by the manifest.
* 69 dictionary entries are marked **requires confirmation**: the export does not define convexity, irregularity, roughness, texture, phase-shift sums, etc.;
  descriptions are name-based. Confirm against the HoloMonitor documentation before presenting definitions.
* Statistics are descriptive; **no p-values** are shown because repeated observations are not independent. Donor is the biological unit once more donors exist.
* Elapsed time is relative to the first frame of each position (positions with different start times are aligned at 0).
* CSV support is limited: single-table CSVs resembling `All cells` / `Frame stats` are read, pairing by name only; untested on real HoloMonitor CSV exports.
* Tracking-only or morphology-only positions are imported with warnings. Only the one sample position was available, so multi-donor behaviour is tested with
  copies of it (synthetic), not real multi-donor data.
* PNG export: camera icon in each plot, or "Prepare PNG" (needs Chrome/Edge for kaleido).
* Very large collections (hundreds of positions) load all Parquet tables into memory; filtering is in-memory. Not benchmarked beyond the sample.
