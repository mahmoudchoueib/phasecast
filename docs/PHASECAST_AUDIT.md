# PhaseCast audit (state at the start of chunk 1)

Audit date: 29 Oct 2026. Git checkpoint: tag `checkpoint-pre-chunk1` (commit `6665af0`). Work for this chunk is on branch
`chunk1-ingestion-plate-mapper`; nothing was overwritten.

## 0. What "existing PhaseCast" turned out to be

The GitHub repository contained an initial commit (a one-line README) and, on top of it, one commit (`6665af0`) holding
the Streamlit dashboard built in an earlier session under the working title "HoloMonitor Explorer". That dashboard is the
only application code that existed, and it **is** the PhaseCast baseline audited here (Python package `hmx`, entry point
`app.py`). There was no React/FastAPI front end and no other framework; none was introduced.

## 1. Existing architecture

* **Stack:** Python 3.12, Streamlit (UI), Plotly (figures), pandas/NumPy/SciPy (analysis), OpenPyXL (Excel read), PyArrow
  (Parquet), pytest. `requirements.txt` lists them. Launch: `streamlit run app.py`.
* **Entry point:** `app.py` → `get_context()` (sidebar, data load, filters) → `st.navigation` → page function.
* **Layers (package `hmx/`):** `discovery` (find + pair files) → `readers`/`parsing` (Excel → tables) → `spec`
  (explicit feature dictionary) → `tracks` (kinematics, lineage, morphology↔tracking link) → `validation` (QC) → `store`
  (ingest to Parquet + load) → `metadata` (sample manifest) → `analysis`/`features` → `viz` → `page_*`/`demo`.
* **Storage and caching:** per-position Parquet tables + `meta.json` in `processed/samples/<id>/`; SHA-256 fingerprints to
  skip unchanged files; import log; generated data dictionary; Streamlit `cache_resource` for the loaded dataset.
* **Pages (8):** Demo Overview, Dataset Overview, Morphology Explorer, Cell Population Distributions, Feature
  Relationships, Cell Tracking Explorer, Early-Window Features, Quality Control & Dictionary.
* **Tests:** 21 pytest tests (parsing, pairing, reconciliation, duplicates, missing files, early-window causality, page render).

## 2. Baseline run (before any change)

| Check | Result |
|---|---|
| `pytest` | **21 skipped** - the tests looked for the sample workbooks in the project root, but the files had since been moved into `raw_data/`, so the suite silently tested nothing |
| Old importer on `raw_data/` (239 files, written to a separate cache) | 181 s; 122 "positions"; 0 failures; 11 discovery problems; 90 MB cache |
| Of those 122 positions | **3 phantom positions**: `A2P1-MOR(BLUR`/`A2P1-TRACKING(BLUR` and `C2P4`/`C2P4-TRACKING#` were not paired because of trailing characters in the file names; `B1P1.xlsx` (no suffix) was recognised only by worksheets |
| Endpoint workbook | reported as "unrecognised file" |
| Donor / plate / well model | donor existed only as a per-position text field in an optional manifest; no concept of plate, no well-level assignment, no evidence trail |

## 3. Working functionality (verified by running it)

Recursive discovery and structure-based classification; parsing of all MOR and TRACKING worksheets (header detection by
content, units preserved, day-first timestamps); per-position Parquet caching with change detection; failure isolation
(one bad pair never stops a batch); tracking analytics (step speeds, displacement, lineage, validated division/cell-cycle
logic, lineage-continuous and field coordinates); exact-value morphology↔tracking linking with validation; QC (count
reconciliation, duplicates, intervals, ranges, definitions of exported tracking columns); early-window feature tables
(proved causal by test); exploratory pages with CSV/PNG export. All eight pages render on the full 120-position data set.

## 4. Missing functionality (the reason for this chunk)

1. Plate and well model; visual plate layout; well-level donor assignment with evidence and history.
2. Handling of ambiguous donor folders (`PLATE2-25F_40M_34F_20M`) and of conflicting file labels.
3. Endpoint workbook ingestion and exact/alias donor linking.
4. Recognition of irregular file names; reporting of cross-folder partner files.
5. Content-level duplicate detection (renamed copies of the same measurements).
6. Inventory, mapping-quality and research-overview pages; PhaseCast visual identity.

## 5. Technical limitations and fragile components found

* **Identity of positions relied on file names.** In `raw_data` the file name and the labels written by the microscope
  disagree for seven positions, and several TRACKING files are renamed copies of another position's tracking.
* **Sample table keyed only by folder + name.** Fine for uniqueness, but carried no plate/well semantics.
* **Tests tied to a fixed location** (see baseline). Fixed: tests now use `raw_data/16M` and skip loudly when absent.
* **Import time** ≈ 3 min for 119 pairs because every sheet is read in pure Python (acceptable with change detection;
  parallel import is a possible later optimisation).
* **Everything is loaded in memory** (≈ 273,000 cell rows, 22,000 tracks for 120 positions - fine; hundreds more positions
  would need lazy loading).
* `Dataset Overview` page duplicated functionality now covered by the new pages (kept as "legacy" rather than deleted).
* Exported *Motility speed* is a windowed mean, tracking and morphology frequently use different segmentations
  (only 1–5 % exact matches in many positions): documented, not a code defect.

## 6. Components preserved (unchanged behaviour)

`readers`, `parsing`, `spec`, `tracks`, `validation`, `analysis`, `features` (endpoint join extended), `viz`, the five analysis
pages, the demo page, the QC/dictionary page, the manifest mechanism (now optional/legacy), the Parquet layout.

## 7. Components refactored or extended

| Component | Change | Why |
|---|---|---|
| `discovery.py` | tolerant file-name regex with qualifiers; endpoint-workbook detection; cross-folder candidate reporting | real file names; endpoint workbook |
| `store.py` | richer `meta.json` (experiment, MOR/TRACKING group labels, content signatures); inventory; endpoint ingestion; stale-sample quarantine; `load_dataset` now applies the mapping layer | plate/donor model |
| `ui_common.py` | PhaseCast branding, plate/donor filters, eligibility switch, cache token covers metadata files | usability and safety |
| `features.join_endpoints` | joins all endpoint columns by exact donor id | real endpoints |
| `app.py` | grouped navigation, new pages, renamed PhaseCast | structure |
| **new** `mapping.py`, `endpoints.py`, `branding.py`, `page_phasecast.py` | donor/plate logic, endpoint parser, identity, five new pages | requested features |

No framework migration and no new dependency were needed.

## 8. Recommended extension strategy (next chunks)

1. Get the PLATE2 layout and the open questions in `SCIENTIFIC_ASSUMPTIONS.md` answered; record decisions in the Plate Mapper.
2. Decide on a policy for positions whose tracking and morphology come from different segmentations.
3. Build donor-level feature tables from eligible positions (already prepared in `features.aggregate_to_donor` / `join_endpoints`).
4. Only then: leave-one-donor-out validation, small interpretable models, uncertainty reporting (n ≤ 12 donors).
