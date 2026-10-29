# PhaseCast data dictionary

Feature-level definitions (every morphology/tracking column with original name, unit, source sheet and
interpretation status) are generated automatically from the imported files into
[`data_dictionary.csv`](data_dictionary.csv) / [`data_dictionary.json`](data_dictionary.json) and are searchable in the
app (*Quality Control & Dictionary → Data dictionary*). This page documents the **structure**: tables, identifier columns,
metadata files and the endpoint columns. "Requires confirmation" means the export does not define the quantity.

## 1. Experimental hierarchy and identifier columns

`donor → experiment/plate → well → imaging position → frame → cell observation`

| Column | Meaning | Where it comes from |
|---|---|---|
| `donor_id` | donor (e.g. `16M`); `Unresolved` = well's donor not yet known; `Unassigned` = no information | folder label or manual decision (`mapping.py`) - never from file content |
| `plate_id` | stable plate id = slug of the experiment text in the file (e.g. `cell_morphology_jonny_plate3`) | file `Experiment` column |
| `plate_label` | display label (`PLATE3`) | experiment text / folder name / your edit in `plates.csv` |
| `well_id` | well, e.g. `B4` | file `Group` text ("Well B4 - Position 2"); overridden only by an explicit resolution |
| `position_id` | imaging position within the well, e.g. `2` | file `Group` text |
| `sample_id` | one MOR/TRACKING pair = one imaging position: `<relative folder>/<file-name key>` (e.g. `16M/A1P6`) | derived; a pairing key, **not** a donor id |
| `frame_no`, `frame_id`, `acquired_at`, `elapsed_h` | frame index, software frame id, acquisition time (day-first), hours since first frame | MOR `Frame stats` |
| `cell_id` | cell label **within a frame** (not persistent) | MOR `All cells` |
| `track_id`, `family`, `parent_id`, `generation` | tracked cell (`Cell 4-1-2`), its family sheet, parent track, division generation | TRACKING sheets / names |

## 2. Tables in the loaded dataset (`Dataset`)

| Table | One row per | Main columns |
|---|---|---|
| `samples` | imaging position | identifiers above, `morphology_file`, `tracking_file`, `n_frames`, `n_cell_obs`, `n_tracks`, `acquisition_start/end`, `link_pct`, mapping and eligibility columns (§3) |
| `frames` | frame of a position | `n_cells`, `confluence_pct`, per-frame software averages (`exp_avg_*`), image-level fields (scale, wavelength, phase shift) |
| `cells` | cell observation (frame × cell) | 38 morphology/optical features (`area_um2`, `perimeter_um`, `hull_convexity`, …) |
| `track_obs` | tracked cell × time | positions, motility columns, `end_status`, lineage columns, inferred link to morphology |
| `track_summary` | tracked cell | duration, accumulated distance, net displacement, directness, speeds, division/cell-cycle fields |
| `track_averages` | time point | software "Averages" sheet |
| `wells` | (plate, well) | `donor_id`, `mapping_status`, `evidence`, `candidates`, `n_positions` |
| `plates` | plate | `plate_label`, `n_rows`, `n_cols`, `geometry_status` (`suggested_unconfirmed` / `user_confirmed`) |
| `endpoints` | donor | `donor_id`, endpoint columns (§4), `row_note`, `endpoint_source` |
| `inventory` | discovered file | `rel_path`, `folder`, `kind`, `pairing_key`, `qualifier`, `size_mb`, `sample_id`, `notes` |

## 3. Position-level mapping and quality columns (`samples`)

| Column | Values / meaning |
|---|---|
| `mapping_status` | `confirmed_manual`, `confirmed_folder`, `unresolved`, `conflict`, `unassigned` (see SCIENTIFIC_ASSUMPTIONS.md) |
| `mapping_evidence`, `donor_candidates` | text evidence for the donor decision; candidate donors when unresolved |
| `label_status` | `consistent` (file name, MOR and TRACKING labels agree), `conflict`, `resolved` (user decision), `unknown` |
| `label_detail` | e.g. "file name says C3 position 6; MOR file says C3 position 6; TRACKING file says D3 position 1" |
| `file_name_well/position`, `mor_well/position`, `trk_well/position` | the three labels |
| `flags` | comma list: `label_conflict`, `experiment_conflict`, `duplicate_acquisition`, `content_duplicate`, `duplicates_folder` |
| `content_duplicate_detail` | which other position holds identical measurements |
| `resolution_action` | user decision: `exclude`, `use_mor_label`, `use_trk_label`, `use_filename_label`, `include_as_is` |
| `analysis_eligible`, `eligibility_reason` | True only if donor confirmed and no unresolved flag; the reason is always given |
| `pair_agreement` | `high (same segmentation)` (≥ 50 % of tracked cells coincide exactly with morphology cells), `partial`, `none`, `n/a` - a **diagnostic**, not a verdict |
| `qualifiers` | text after MOR/TRACKING in file names, e.g. `(BLUR`, `#` (meaning unconfirmed) |
| `has_endpoint` | donor has an endpoint row (exact id or alias) |
| `duplicate_of` | sample id of a byte-identical earlier import (excluded from tables) |

## 4. Endpoint columns (`Combined Data Jonny Project.xlsx`)

| Internal name | Original header | Unit | Interpretation |
|---|---|---|---|
| `donor_id` | `Donor` | – | exact id, trimmed and upper-cased (`16M`) |
| `fi_pct` | `FI (%)` | % | fusion index as exported (counting rule not stated) |
| `ache_medium_u_ml` | `Medium Mean (U/mL)` | U/mL | **requires confirmation**: the project brief says "AChE membrane", the header says "Medium"; 5 negative values kept |
| `ache_lysate_u_ml` | `Lysate Mean (U/mL)` | U/mL | acetylcholinesterase activity in lysate |
| `desmin_pct` | `Desmin %` | % (from header text) | desmin-positive percentage; assay not described |
| `row_note` | unnamed column F | – | free text kept verbatim (`44.6?` for 25F); which value it refers to is unknown |

## 5. Editable metadata files (`metadata/`)

| File | Columns |
|---|---|
| `plates.csv` | `plate_id, plate_label, experiment_name, n_rows, n_cols, geometry_status, notes` |
| `donor_map.csv` | `plate_id, well_id, donor_id, status, evidence, updated_at` |
| `donor_map_history.csv` | `timestamp, plate_id, well_id, old_donor_id, old_status, new_donor_id, new_status, evidence` |
| `donor_aliases.csv` | `alias, donor_id, note` |
| `sample_resolution.csv` | `sample_id, action, evidence, updated_at` |
| `../manifest.csv` (optional, legacy) | `sample_key, donor_id, run_id, well_id, position_id, passage, culture_date, fusion_index, notes` |

## 6. Processed storage (`processed/`)

`samples/<id>/{frames,cells,track_obs,track_summary,track_averages}.parquet` + `meta.json` (fingerprints, experiment and
group labels, content signatures, QC issues, column provenance); `endpoints/*.parquet|json`; `inventory.parquet`;
`discovery.json`; `import_log.jsonl`; `_stale/` (positions whose source files disappeared - moved, never deleted).
Float columns are stored as float32 (the precision of the instrument export).
