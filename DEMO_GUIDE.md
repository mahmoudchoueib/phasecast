# Five-minute demo for Richard

Start: `streamlit run app.py`, open **Demo Overview** (first page). Close the sidebar's import box for screen sharing.

**0:00 – Framing (30 s).** One position (well A1, position 6; 145 frames, 72 h, 30-min interval; 2,137 cell observations, 191 tracked cells).
Exploratory only; donor is "Unassigned" until a manifest is supplied. Point out the grey "what cannot be inferred" box under every figure.

**0:30 – Morphology over time (60 s).** Figures 1 and 2: mean cell area and hull convexity with IQR bands. Read the auto-generated observation
(area is lower in the last 6 h than the first 6 h in this sample) and the caution: averages change as cells divide/leave, not necessarily cells changing.

**1:30 – Distributions (45 s).** Figure 3, early vs late area. Then open **Cell Population Distributions** and flip window presets (0–6 … 0–72 h); show
heterogeneity tab. Mention: effect sizes only, no p-values (repeated observations).

**2:15 – Relationships (30 s).** Figure 4, area vs perimeter: strong by geometry, not biology. In **Feature Relationships** try
hull convexity vs irregularity, colour by elapsed time.

**2:45 – Tracking (75 s).** Figure 5, then **Cell Tracking Explorer**: trajectories of one family (Cell 4 and descendants) in "Family frame",
the divisions tab (60 divisions, 41 complete cycles, mean 5.84 h - matches the software's own number), and say why: the app verified how divisions are encoded.
Highlight the discovery that daughters' positions are relative to the parent's last position.

**4:00 – Why it will scale (60 s).** **Early-Window Features**: choose cutoffs 6/12/24 h, show the table, download CSV/Parquet; the
`window_max_elapsed_h` column proves no later data is used (also unit-tested). **Dataset Overview → Sample metadata**: donors/fusion index
go in the manifest, new files just need a rescan. **Quality Control**: reconciliations pass, 7 tracks with empty End status flagged, "requires confirmation" dictionary entries.

Questions to ask Richard: definitions of Hull/Shape convexity, Irregularity, Roughness, Texture; the speed-window setting in the tracking software;
what the morphology edge-cell exclusion means for tracking; planned donor/run/well naming for the manifest.
