# PhaseCast developer walkthrough

Written for someone learning scientific programming. It explains how data flows through PhaseCast and the
programming ideas behind each step. File names refer to the `hmx/` package.

## 1. The big picture

```
 raw_data/  (Excel files, never modified)
     │
     ▼  discovery.py     "what files are there and which belong together?"
 file list + pairs
     │
     ▼  parsing.py       "read the sheets, find headers, convert units/time, keep original names"
 per-position tables
     │
     ▼  tracks.py / validation.py    "derive per-track statistics, check everything, flag problems"
     │
     ▼  store.py (ingest)            "save as Parquet + meta.json, skip unchanged files"
 processed/  (fast cache, safe to delete and rebuild)
     │
     ▼  store.py (load_dataset)      "stack all positions, then JOIN donor/plate information"
 Dataset object  ◄──── mapping.py (donor decisions)  ◄──── metadata/*.csv (editable)
     │            ◄──── endpoints.py (fusion index etc.)
     ▼  ui_common.get_context()      "sidebar filters -> a filtered Dataset"
 page functions (page_*.py, demo.py) draw tables and Plotly figures
```

A **position** (one `-MOR` + one `-TRACKING` file = one field of view in one well) is called a *sample*. Everything
is stored with the hierarchy

`donor → experiment/plate → well → imaging position → frame → cell observation`

as columns (`donor_id`, `plate_id`, `well_id`, `position_id`, `frame_no`, `cell_id`), so a row can always be traced
back to where it came from. Cells, frames, positions and wells are **never** treated as independent donors.

## 2. Key programming ideas used (in plain language)

**DataFrame.** A table held in memory (pandas). Rows are records, columns are fields. Almost every function in
PhaseCast takes a DataFrame and returns a DataFrame. Example: the `samples` table has one row per position; the
`cells` table has one row per cell per frame (about 273,000 rows for all donors).

**Parsing.** Turning a file made for humans into tables a program can use. An Excel export has titles, merged
banner rows ("Frame Info", "Cell feature data"), and units in the header text ("Area (µm²)"). `parsing.py` finds the
header row *by content* (not "row 4") and splits `Area (µm²)` into a name and a unit, but keeps the original text so
nothing is lost.

**Explicit mapping.** `spec.py` contains a dictionary from original column names to internal names
(`"Area (µm²)" → area_um2`). We never auto-rename: an unknown column keeps an `x_…` name and is flagged. This keeps
the link to the instrument's wording auditable.

**Grouping (`groupby`).** "Split a table into groups, summarise each group." `samples.groupby("donor_id")` makes one
group per donor; `.agg(...)` then counts positions or sums frames. The Plate Mapper uses `groupby("well_id")` to
collapse positions into wells, and `donor_summary()` groups wells and positions into donors.

**Join / merge.** Combine two tables by matching a key column - like a spreadsheet VLOOKUP, but for whole tables.
`apply_mapping()` merges the *well → donor* table onto the *positions* table using `(plate_id, well_id)`.
`load_dataset()` merges the hierarchy columns onto every cell row using `sample_id`. A **left join** keeps all rows of
the left table and fills `NaN` (empty) where there is no match - that is how endpoint values stay empty for donors
without an endpoint row instead of being invented.

**Caching.** Reading 119 Excel pairs takes about three minutes; reading the saved Parquet files takes seconds.
`store.ingest` therefore saves every position as Parquet once and records a **fingerprint** (file size, modified time,
SHA-256 hash). On the next scan, files whose fingerprint is unchanged are skipped. In the app,
`st.cache_resource` keeps the loaded `Dataset` in memory; a *token* (modification times of everything the loader reads)
is part of the cache key, so saving a donor assignment invalidates the cache automatically.

**Parquet.** A compact, typed, column-oriented file format (much faster than CSV/Excel for analysis). The `processed/`
folder is derived data: delete it and re-import whenever you like.

**Streamlit rerun model.** Streamlit executes the script from top to bottom *every time you interact*. `app.py` builds
the sidebar and menu; the chosen page function then runs. State that must survive a rerun lives in `st.session_state`
(e.g. the selected well). `st.form` groups several inputs so they are submitted together.

## 3. Module guide (what to read, in order)

| Step | File | What to look for |
|---|---|---|
| 1 | `app.py` | The four steps of the app; `SECTIONS` is the menu. |
| 2 | `hmx/ui_common.py` | `get_context()` - sidebar, cached load, filters. `Ctx` = what every page receives. |
| 3 | `hmx/page_phasecast.py` | New pages. `_plate_grid` (table → grid of wells), `_plate_figure` (drawing), `plate_mapper` (page). |
| 4 | `hmx/mapping.py` | The donor/plate logic: `assess_samples` → `effective_well_table` → `apply_mapping`. Heavily commented. |
| 5 | `hmx/endpoints.py` | Reading the endpoint workbook; exact/alias donor linking. |
| 6 | `hmx/store.py` | `ingest` (write side) and `load_dataset` (read side; its docstring lists the six data-flow steps). |
| 7 | `hmx/discovery.py` | File finding, kinds, pairing rules, cross-folder candidates. |
| 8 | `hmx/parsing.py`, `hmx/spec.py` | Sheet parsing and the explicit feature dictionary. |
| 9 | `hmx/tracks.py`, `hmx/validation.py` | Track kinematics, lineage, exact-value morphology↔tracking link, QC checks. |
| 10 | `hmx/analysis.py`, `hmx/features.py`, `hmx/viz.py` | Statistics, early-window features, figures. |

## 4. Following one position through the system

Take `raw_data/16M/A1P6-MOR.xlsx` and `A1P6-TRACKING.xlsx`.

1. **discovery** classifies both files from their worksheet names and pairs them: same folder, same key `A1P6`.
   Sample id = `16M/A1P6`.
2. **parsing** reads `Frame stats` (145 frames), `All cells` (2,137 cells), `Frame info`, and in the tracking file the
   per-family sheets (191 tracked cells). Time stamps are parsed *day-first* and converted to elapsed hours.
3. **tracks** computes per-track speed/displacement, finds divisions, and links tracking rows to morphology cells where
   the frame, area and optical volume are exactly equal.
4. **store.ingest** writes Parquet tables and `meta.json` (including the experiment text, the MOR and TRACKING `Group`
   labels, and content signatures).
5. **load_dataset → assess_samples** derives `plate_id` from the experiment text and compares three labels for the
   position: file name (`A1P6`), MOR `Group` ("Well A1 - Position 6") and TRACKING `Group`. They agree → `consistent`.
6. **effective_well_table** reads the folder name `16M`: exactly one donor label → well `A1` of this plate gets donor
   `16M`, status `confirmed_folder`. (For the `PLATE2-25F_40M_34F_20M` folder there are four labels → status
   `unresolved`, no donor assigned.)
7. **apply_mapping** merges that onto the position and decides `analysis_eligible`.
8. The sidebar filters narrow the table; a page draws it.

## 5. Where decisions are stored and why

Donor decisions are **data**, not code: small CSV files in `metadata/` that you can open in Excel.

| File | Written by | Purpose |
|---|---|---|
| `plates.csv` | Plate Mapper → "Confirm geometry" | plate label, rows × columns, status |
| `donor_map.csv` | Plate Mapper → "Save assignment" | one decision per (plate, well) with evidence |
| `donor_map_history.csv` | automatically | append-only audit log of every change |
| `donor_aliases.csv` | Endpoint Data Preview | explicit alias → donor id |
| `sample_resolution.csv` | Data Quality and Mapping | per-position decisions (exclude / which label to trust) |

Because the logic that *reads* these files is separate from the logic that *parses Excel*, you can change a donor
assignment without re-importing anything.

## 6. How to extend PhaseCast

* **A new page**: write `def my_page(ctx): ...` in a `page_*.py` file (use `ctx.ds` for filtered analysis data,
  `ctx.full` for everything) and add it to `SECTIONS` in `app.py`.
* **A new feature column**: add a `Feature(...)` row to `spec.py`; it is picked up by the parser, dictionary and plots.
* **A new donor label convention**: change `DONOR_TOKEN_RE` in `mapping.py`.
* **A new endpoint column**: add it to `ENDPOINT_MAP` in `endpoints.py`.
* **After changing parsing logic**: bump `PARSER_VERSION` in `hmx/__init__.py`; the next scan rebuilds the cache.

## 7. Testing

`pytest -q` runs 49 tests: pure logic (mapping, pairing, endpoints) on small synthetic tables, plus checks on the real
data (frame counts reconcile with cell rows; unresolved wells are not eligible), plus a headless render of every page.
Tests that touch the donor mapping use a temporary, empty metadata folder, so your own saved decisions never change
test outcomes.
