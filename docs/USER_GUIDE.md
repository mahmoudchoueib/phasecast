# PhaseCast user guide

## Starting PhaseCast (Windows PowerShell)

```powershell
cd "C:\Studies\Phd\Project 31\data"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The app opens at http://localhost:8501 (paste it into a browser if it does not open by itself). Leave the PowerShell
window open while you work; press `Ctrl+C` there to stop.

If the `.venv` folder is missing: `python -m venv .venv`, then
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.

## First time: import the raw data

1. Put the exports in `raw_data\` (any sub-folder structure; folders named like `16M` identify donors).
2. In the sidebar open **Data location & import** and click **Scan folder and import new / changed files**. The first
   import of 119 position pairs takes about three minutes; later scans only process new or changed files.
   (Command line equivalent: `.\.venv\Scripts\python.exe -m hmx.cli ingest`.)

Original Excel files are only ever read.

## Menu

| Section | Page | Use it to |
|---|---|---|
| Project | **Research Overview** | see the project question, readiness per donor, and what still needs a decision |
| Data | **Plate Mapper** | see each plate, donors by colour, files per well; assign wells to donors |
| Data | **Dataset Inventory** | every file, position, folder and donor discovered |
| Data | **Endpoint Data Preview** | fusion index, desmin and AChE per donor, with units and warnings |
| Data | **Data Quality and Mapping** | positions held out of analysis and why; record decisions |
| Analysis | Morphology Explorer, Cell Population Distributions, Feature Relationships, Cell Tracking Explorer, Early-Window Features | the analysis pages (unchanged functionality) |
| Reference | Demo Overview, Quality Control & Dictionary, Dataset Overview (legacy) | presentation figures, per-file QC, data dictionary |

### Sidebar filters (analysis pages)

Plate, donor, well, position, file. The switch **Include unresolved / flagged positions** is *off* by default: only
positions whose donor is confirmed and that carry no unresolved flag are analysed. The Plate Mapper, Inventory and
Data Quality pages always show everything.

## Using the Plate Mapper

1. **Choose the experiment/plate** at the top. The experiment name stored in the files is shown under it.
2. **Confirm the plate geometry** (expander). The files do not contain the plate format, so PhaseCast only *suggests*
   one (24-well, 4 × 6). Choose the real format and click *Confirm geometry and label*.
3. **Read the plate.** Each circle is a well, coloured by donor. Grey = donor unknown/unresolved, white outline = no data.
   Ring colours: **red** = a MOR or TRACKING file is missing for a position in that well; **amber** = flagged
   position(s); dark ring = selected well.
4. **Hover** over a well for donor, positions, files, frames and dates.
5. **Click** a well (or choose it in *Selected well*). The right-hand panel shows plate ID, well ID, donor (if confirmed),
   mapping status and evidence, every imaging position with MOR/TRACKING availability, acquisition date, frames,
   label agreement and eligibility.
6. **Highlight a donor** to dim every other well, and use the **Donor summary** table (wells, positions, files, eligible
   positions) for this plate or all plates.

### Resolving the four-donor plate (PLATE2)

The folder `PLATE2-25F_40M_34F_20M` lists four donors but the files do not say which well holds which. PhaseCast shows the
wells in grey as **unresolved**, with the four candidates listed, and does not assign anything. When you learn the layout
(e.g. from a plate sheet or from Prof. Day):

1. Select the wells, open **Assign wells to a confirmed donor**, pick the donor (candidates are listed first).
2. Type the **evidence** (required) - e.g. "plate layout sheet from Jonny, row A".
3. **Save assignment.** The decision is written to `metadata\donor_map.csv` and the change to `donor_map_history.csv`.
   To correct it later, assign the wells again (the history keeps both) or use *Remove manual decision* to fall back to
   the folder evidence. *Mark as unresolved* records an explicit "unknown".

Tip: tick *Also include every UNRESOLVED well on this plate* to apply one decision to many wells.

## Endpoint Data Preview

Shows the 12-donor endpoint table with the original headers, units, interpretation status ("requires confirmation" for the
`Medium Mean` column) and every issue found (negative values, the `44.6?` note). The linking table shows which imaging
donors have an endpoint row (exact match or explicit alias only). Aliases are edited in the *Donor aliases* expander
(columns `alias`, `donor_id`).

## Data Quality and Mapping

* **Held-out positions** - why each is excluded (donor unresolved; file-name/MOR/TRACKING labels disagree; same data as
  another position; file in a `DUPLICATES` folder; ...).
* **Label conflicts** - the three labels of each position side by side, plus a form to record a decision
  (exclude / trust MOR / trust TRACKING / trust file name / include as is) **with evidence**.
* **File pairing** - unmatched files and cross-folder candidates (never paired automatically).
* **Well mapping** and **Resolution log** - current donor per well and every decision made.

## Tips

* The pair-agreement column (*Dataset Inventory → Positions*) says how many tracked cells coincide exactly with an exported
  morphology cell. "none"/"partial" usually means tracking and morphology used different segmentation settings, not that
  the files are wrong.
* Everything you decide is plain CSV in `metadata\`; you can open it in Excel, and you can delete a file to undo all
  decisions it contains.
* `processed\` is a cache; deleting it only costs the three-minute re-import.
