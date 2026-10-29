Addition from #main - i have added 12 donors instead of one test file

# phasecast
AI-driven analysis of longitudinal quantitative phase imaging for early, non-destructive prediction of regenerative cell quality/potency

---

# PhaseCast - forecasting cell potency from early quantitative phase imaging

PhaseCast is a local research dashboard (Python, Streamlit, Plotly, pandas) for HoloMonitor holographic-microscopy
exports of human skeletal muscle-derived cells. It reads morphology (`-MOR.xlsx`) and tracking (`-TRACKING.xlsx`)
exports from many donors, wells and imaging positions, links them to donor-level biological endpoints (fusion index,
desmin, acetylcholinesterase) **only where donor identity is confirmed**, and provides exploratory analysis and
early-window feature engineering for later prediction work.

**Current status (chunk 1):** data foundations - ingestion, plate/donor mapping, endpoint integration, data quality,
and the exploratory analysis pages. **No prediction models and no endpoint correlations yet** (see
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)). Original Excel files are only ever read.

## Launch (Windows PowerShell)

```powershell
cd "C:\Studies\Phd\Project 31\data"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open http://localhost:8501. First time: sidebar → *Data location & import* → **Scan folder and import** (about 3 minutes
for 119 position pairs; later scans only process new/changed files). Setup from scratch:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Tests: `.\.venv\Scripts\python.exe -m pytest -q`.

## Pages

| Section | Page | Purpose |
|---|---|---|
| Project | Research Overview | project question, per-donor readiness, items needing a decision |
| Data | **Plate Mapper** | interactive plate layouts, donor colours, per-well details, audited manual donor assignment |
| Data | Dataset Inventory | every file / position / folder / donor discovered |
| Data | Endpoint Data Preview | 12-donor endpoint table, units, warnings, exact/alias donor linking |
| Data | Data Quality and Mapping | held-out positions, label conflicts, pairing problems, decisions with evidence |
| Analysis | Morphology Explorer · Cell Population Distributions · Feature Relationships · Cell Tracking Explorer · Early-Window Features | exploratory analysis (unresolved/flagged positions excluded by default) |
| Reference | Demo Overview · Quality Control & Dictionary · Dataset Overview (legacy) | presentation figures, QC, data dictionary |

## Data layout expected

```
raw_data/
  16M/  A1P2-MOR.xlsx  A1P2-TRACKING.xlsx  ...      <- folder named <age><M|F> = donor label (user's filing)
  PLATE2-25F_40M_34F_20M/ ...                        <- several labels = ambiguous -> wells stay UNRESOLVED
  Combined Data Jonny Project.xlsx                   <- endpoint workbook (first header cell "Donor")
metadata/   plates.csv  donor_map.csv  donor_map_history.csv  donor_aliases.csv  sample_resolution.csv   (editable, created by the app)
processed/  Parquet cache (safe to delete)
```

Plate, well and position come from the text inside each file ("Well A1 - Position 6"); the plate is identified by the
experiment name stored in the files, so identical well names on different experiments never collide.

## Principles

* Donor → experiment/plate → well → position → frame → cell hierarchy preserved; cells/frames/positions/wells are not donors.
* No donor is inferred from age, sex, morphology, motility or endpoint values; donor names are matched exactly or through an explicit alias table.
* Unresolved or conflicting positions are excluded from donor-level analyses by default; every decision is stored with its evidence and an audit trail.
* Nothing is silently changed or removed: suspicious values are flagged, not fixed.

## Documentation

[Audit](docs/PHASECAST_AUDIT.md) · [User guide](docs/USER_GUIDE.md) · [Developer walkthrough](docs/DEVELOPER_WALKTHROUGH.md) ·
[Data dictionary](docs/DATA_DICTIONARY.md) · [Scientific assumptions](docs/SCIENTIFIC_ASSUMPTIONS.md) ·
[Implementation status](docs/IMPLEMENTATION_STATUS.md) · [Demo guide](DEMO_GUIDE.md)

Package layout: `app.py` (entry), `hmx/` (discovery, parsing, spec, tracks, validation, mapping, endpoints, store, analysis,
features, viz, branding, page_*), `tests/`, `docs/`, `.streamlit/config.toml` (theme).
