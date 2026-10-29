# Implementation status - chunk 1 of 4

Status key: **Tested** = exercised by an automated test and/or run against the real data in this session ·
**Implemented** = written and renders but only partly tested · **Not started**.
Test run at the end of this chunk: `pytest -q` → **51 passed** (≈ 53 s) · full import of `raw_data` → 120 positions,
0 failures · incremental re-scan → 120 unchanged, 0 re-imported (15.7 s) · `streamlit run app.py` health check "ok".

## Part A - audit
| Item | Status |
|---|---|
| `docs/PHASECAST_AUDIT.md` (architecture, baseline run, gaps, strategy); git tag `checkpoint-pre-chunk1`; work on branch | Done |
| New identity (logo, palette, theme, hero banners) | Implemented (checked on rendered figures; the live browser UI was not visually inspected end-to-end) |

## Part B/C - data understanding and ingestion
| Item | Status |
|---|---|
| All 238 MOR/TRACKING files + endpoint workbook scanned; structures documented | Tested (scan) |
| Recursive discovery, MOR/TRACKING classification (name + worksheets), pairing | Tested |
| Irregular names (`(BLUR`, `#`, no suffix) paired correctly | Tested |
| Cross-folder partner files reported, never auto-paired | Tested |
| Experiment name → stable plate id; plate/well/position from `Group` text; timestamps, units, frame/cell counts | Tested |
| Source path preserved; originals never modified | Tested (size check, read-only code paths) |
| Duplicate detection: byte-identical (existing) **and** content-identical measurements (new) | Tested |
| Missing/ambiguous pairs and data-quality report | Tested (QC page, Data Quality and Mapping page) |
| Parquet cache with change detection; stale positions quarantined to `processed/_stale/` | Tested |

## Part D - Plate Mapper
| Item | Status |
|---|---|
| Experiment/plate selection, configurable geometry with explicit confirmation | Implemented; confirm-geometry button not automatically tested |
| Plate grid, donor colours, hover details, highlight donor, missing-file / flagged / unassigned markers | Implemented; figure rendered and inspected |
| Click a well to select it (Plotly selection) | **Implemented but not automatically tested** (headless runner cannot click a chart); a "Selected well" dropdown gives the same function |
| Well detail panel (plate, well, donor, status, positions, MOR/TRACKING, dates, frames) | Tested (renders on real data) |
| Donor summary (wells, positions, files, eligible) | Tested (unit test + render) |
| Save / correct / clear manual assignments with mandatory evidence and history | Tested (unit tests and a scripted UI-form run against a temporary folder) |

## Part E - ambiguous donor mapping
| Item | Status |
|---|---|
| Four candidate labels of `PLATE2-25F_40M_34F_20M` detected and displayed; 19 wells / 40 positions left **unresolved**, none assigned | Tested |
| Unresolved/conflicting positions excluded from analyses by default (sidebar switch to include) | Tested |
| Editable mapping files with audit trail | Tested |

## Part F - endpoints
| Item | Status |
|---|---|
| Endpoint workbook parsed (12 donors, one row each - verified), units, missing values, negative values and the "44.6?" note flagged | Tested |
| Exact donor linking and explicit aliases; no fuzzy matching | Tested |
| Endpoints joined to imaging donors only via confirmed donors | Tested (join leaves unresolved donors empty) |

## Part G - interface
Research Overview, Plate Mapper, Dataset Inventory, Endpoint Data Preview, Data Quality and Mapping: **Tested** (render on real
data). Existing analysis pages: **Tested** (all render on the 120-position data set).

## Part H/I - code explanation, documentation, tests
Docstrings on every function plus explanatory module docstrings; `docs/DEVELOPER_WALKTHROUGH.md`; README, USER_GUIDE, DATA_DICTIONARY, SCIENTIFIC_ASSUMPTIONS written. 51 tests (21 previously skipped tests restored and repointed; 30 new).

## Known gaps and risks
* **Donor of PLATE2 wells is unknown** - needs the plate layout (cannot and must not be inferred).
* **D2 well on PLATE3** is claimed by both folders `32M` and `35M` (a `D2P6` TRACKING file filed in `35M`; its content matches the MOR file in `32M` at 78.8 % exact-value agreement versus 0 % for a control). Held out as a *conflict* until a decision is recorded.
* 49 of 120 positions are currently held out of analysis (40 PLATE2, plus label conflicts, content duplicates, `DUPLICATES` folder, D2 conflict); 71 are eligible. Resolving them is a user/supervisor task.
* Plate geometry (4 × 6) is only *suggested* until confirmed in the app.
* Morphology and tracking come from different segmentations for ~43 % of positions (pair agreement "partial/none"): limits per-cell joins, to be decided before modelling.
* Import is sequential (~3 min for 119 pairs); memory use will need attention beyond a few hundred positions.
* Streamlit's chart-click selection depends on the installed Streamlit version (1.66 here).
* Not started (later chunks): donor-level feature tables with endpoint correlation, leave-one-donor-out validation, prediction models, uncertainty reporting.
