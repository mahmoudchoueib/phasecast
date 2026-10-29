# Scientific assumptions and limitations (PhaseCast, chunk 1)

PhaseCast is exploratory software for a project asking whether early quantitative phase imaging (QPI) of human
skeletal muscle-derived cells can predict later myogenic potency. This page lists what is *assumed*, what is *verified
from the files*, and what is *not known*. Anything marked **needs confirmation** should be checked with Prof. Day /
the experimenter before it is relied on in a thesis or paper.

## A. Verified from the supplied files

| Statement | How verified |
|---|---|
| The endpoint workbook has **one row per donor**, 12 donors: 16M, 17F, 20F, 20M, 22M, 25F, 25M, 31F, 32M, 34F, 35M, 40M | read directly; duplicates test in code |
| Endpoint columns: FI (%), Medium Mean (U/mL), Lysate Mean (U/mL), Desmin % | header row |
| Every MOR/TRACKING file contains the experiment name and a `Group` text "Well X - Position N" | scanned all 238 files |
| Experiment text distinguishes three experiments: `…Jonny PLATE3` (acquired 21 Jul 2026), `…plate4-Jonny` (28 Jul 2026), `Cell Morphology 2--Jonny 2026/7/1` (1 Jul 2026) | scanned all files |
| Imaging: 145 frames at 30 min (≈72 h) for nearly all positions (exceptions flagged, e.g. 71 and 144 frames) | scanned all files |
| Timestamps are day-first (`dd/mm/yyyy hh:mm:ss`) | dates such as 24/07/2026 occur |
| Morphology `Cell ID` is a per-frame label, not a persistent identity | same cell has different IDs across frames |
| Tracking "Relative pos" is measured from the parent's last position for daughter cells | checked against centroids |
| A MOR file and a TRACKING file from the same acquisition share exact (frame, area, optical-volume) values | genuine pairs link 50–90 %, unrelated files 0 % |

## B. Assumptions made (and how to override them)

1. **Donor identity from folder names.** A raw-data folder named like `16M` is taken as the researcher's own statement that
   everything inside came from donor `16M` (status `confirmed_folder`). This is *the user's filing*, not information in the
   files. Override: assign wells manually in the Plate Mapper (a manual decision always wins).
2. **Donor label convention.** `<age><M|F>` (regular expression in `mapping.py`). A folder name with several such labels is
   treated as ambiguous, never as a sequence of wells.
3. **Plate identity = experiment text.** Two files belong to the same plate if the experiment text in the file is identical.
   Identical well names on different experiments are therefore different wells (e.g. `B3` on PLATE3 ≠ `B3` on PLATE4).
   The *label* PLATE2/3/4 is a display name taken from the experiment text, or from the folder name where the text has none.
4. **Plate geometry.** Not stored in the files. PhaseCast *suggests* the smallest standard format containing every observed
   well label (24-well, 4 × 6, for all three plates) and marks it `suggested_unconfirmed` until you confirm it. **Needs
   confirmation.**
5. **A position's well/position** is read from the files' `Group` text. If it disagrees with the file name, or the MOR and
   TRACKING files disagree, the position is `conflict` and held out until a person decides.
6. **Donor-level units.** Wells and positions are technical replicates *within* a donor. Cells and frames are repeated
   measures of the same sample. Donors are the biological replicates (n = 12 at most).
7. **Time.** Elapsed time is measured from the first frame of each position; positions on the same plate started within
   minutes of each other but are not synchronised to a common clock.

## C. What is deliberately *not* done

* No donor is ever inferred from age, sex, morphology, motility or endpoint values.
* No approximate string matching of donor names. Different spellings are linked only through an explicit alias table.
* No automatic pairing of a MOR file with a TRACKING file from another folder, even when names match (reported instead).
* Unresolved wells are not assigned to the four candidate donors, and are excluded from donor-level work by default.
* No correlation with endpoints, no model fitting, no significance testing in this chunk.
* No data are altered. Suspicious values (negative enzyme activity, an unexplained "44.6?" note) are flagged and kept.

## D. Open questions for the supervisors (**need confirmation**)

1. **Which wells of `PLATE2-25F_40M_34F_20M` belong to which donor?** The files record only well and position. On the other
   two plates donors occupy consecutive wells (PLATE3 in row order, up to four wells per donor; PLATE4 down a column), but PLATE2
   uses wells in every row and column, so its layout cannot be inferred. A plate-layout sheet is needed.
2. Is `Medium Mean (U/mL)` the "AChE membrane" measurement named in the project brief, or a different (culture-medium)
   measurement? PhaseCast keeps the original header and marks the interpretation *requires confirmation*.
3. Five donors (25F, 25M, 31F, 32M, 35M) have **negative** values in that column. Is it blank-subtracted? Are negatives
   to be treated as zero or as missing? (Left unchanged.)
4. The note `44.6?` beside donor 25F: which value does it question (FI 44.49?) and what is the correct value?
5. Did the 25F wells appear on two plates (PLATE3 folder `25F` and as a candidate in PLATE2)? Are they the same donor
   culture and passage?
6. What are the folder `25F/DUPLICATES` files, and the `(BLUR` / `#` file-name qualifiers on three PLATE2 files? They are
   kept and flagged, not interpreted.
7. Plate 2 was acquired on 1 July 2026 and plates 3/4 on 21/28 July: are seeding conditions, passage and time in culture
   comparable? (Possible batch effect.)
8. Morphology and tracking frequently come from **different segmentations** (only 1–5 % of tracked cells coincide exactly
   with an exported morphology cell in many positions). Were the MOR analyses and the tracking analyses run with the same
   settings? This limits per-cell joins of morphology and motility.

## E. Statistical cautions to keep in mind

* With n ≤ 12 donors, any association with endpoints has very low power and a high risk of overfitting; many imaging
  features × 12 donors needs strict validation (leave-one-donor-out), planned for later chunks.
* The same donor appears on several wells/plates; hold whole donors out together when validating.
* Folder-derived donor assignment would silently propagate a filing error. This is why every mapping carries its evidence
  and why the first checks of PhaseCast are about *identity*, not statistics.

