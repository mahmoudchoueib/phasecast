"""Explicit feature mapping and data dictionary for HoloMonitor exports.

Original column names (including units) are the *keys*; internal names are only assigned through
this explicit mapping. Columns that are not in the mapping are kept with an auto-generated
``x_<slug>`` name and flagged in the data dictionary as ``mapping = auto``.

Interpretation status values
----------------------------
``as exported``           name and unit are taken verbatim from the export header; the meaning is
                          self-evident from the name.
``validated in data``     meaning was checked arithmetically against other columns of the export
                          (see ``hmx.validation.track_consistency``).
``requires confirmation`` the export does not define the feature; the note is an inference from
                          the name only and must be confirmed against the HoloMonitor documentation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

AS_EXPORTED = "as exported"
VALIDATED = "validated in data"
CONFIRM = "requires confirmation"

UNIT_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<unit>[^()]*)\)\s*$")


_NOT_A_UNIT = {"migration vs motility"}


def split_name_unit(original: str) -> tuple[str, str | None]:
    """'Area (µm²)' -> ('Area', 'µm²'). Names without a trailing bracket have no unit."""
    m = UNIT_RE.match(str(original))
    if m and m.group("unit").strip().lower() not in _NOT_A_UNIT:
        return m.group("name").strip(), m.group("unit").strip()
    return str(original).strip(), None


def slugify(text: str) -> str:
    """Make a safe lowercase identifier from free text (used only for unmapped columns)."""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(text)).strip("_").lower()
    return s or "unnamed"


@dataclass(frozen=True)
class Feature:
    internal: str
    original: str          # as it appears in the workbook header (with unit)
    level: str             # cell | frame | image | track_obs | track_matrix
    description: str
    status: str = AS_EXPORTED
    label: str | None = None   # nicer plot label


# --- cell-level morphology ("All cells" sheet) -------------------------------------------------
_SHAPE_NOTE = "Definition not stated in the export; interpretation inferred from the name only"

CELL_FEATURES: list[Feature] = [
    Feature("area_um2", "Area (µm²)", "cell", "Area of the segmented cell region.", AS_EXPORTED, "Cell area"),
    Feature("boxed_breadth_um", "Boxed breadth (µm)", "cell", "Breadth of the cell's bounding box (name-based).", CONFIRM),
    Feature("boxed_center_x_um", "Boxed center pos X (µm)", "cell", "X position of the bounding-box centre in the image.", AS_EXPORTED),
    Feature("boxed_center_y_um", "Boxed center pos Y (µm)", "cell", "Y position of the bounding-box centre in the image.", AS_EXPORTED),
    Feature("boxed_length_um", "Boxed length (µm)", "cell", "Length of the cell's bounding box (name-based).", CONFIRM),
    Feature("centroid_x_um", "Centroid pos X (µm)", "cell", "X position of the cell centroid in the image.", AS_EXPORTED),
    Feature("centroid_y_um", "Centroid pos Y (µm)", "cell", "Y position of the cell centroid in the image.", AS_EXPORTED),
    Feature("eccentricity", "Eccentricity", "cell", f"{_SHAPE_NOTE} (0 = circular, towards 1 = elongated).", CONFIRM),
    Feature("hull_convexity", "Hull convexity", "cell", f"{_SHAPE_NOTE}; presumably relates the cell region to its convex hull.", CONFIRM, "Hull convexity"),
    Feature("irregularity", "Irregularity", "cell", f"{_SHAPE_NOTE}; higher presumably means a less regular outline.", CONFIRM),
    Feature("opl_avg_um", "Optical path length avg (µm)", "cell", "Mean optical path length over the cell (name-based).", CONFIRM),
    Feature("opl_max_um", "Optical path length max (µm)", "cell", "Maximum optical path length within the cell (name-based).", CONFIRM),
    Feature("optical_thickness_avg_um", "Optical thickness avg (µm)", "cell", "Mean optical thickness over the cell.", AS_EXPORTED, "Optical thickness (mean)"),
    Feature("optical_thickness_max_um", "Optical thickness max (µm)", "cell", "Maximum optical thickness within the cell.", AS_EXPORTED),
    Feature("optical_volume_um3", "Optical volume (µm³)", "cell", "Optical volume of the cell (integral of optical thickness over area; name-based).", CONFIRM, "Optical volume"),
    Feature("peak_x_um", "Peak pos X (µm)", "cell", "X position of the peak (maximum) value within the cell (name-based).", CONFIRM),
    Feature("peak_y_um", "Peak pos Y (µm)", "cell", "Y position of the peak (maximum) value within the cell (name-based).", CONFIRM),
    Feature("perimeter_um", "Perimeter length (µm)", "cell", "Length of the cell outline.", AS_EXPORTED, "Perimeter"),
    Feature("phaseshift_avg", "Phaseshift avg", "cell", "Mean phase shift within the cell; unit not stated in this sheet (Frame info lists phase shift in rad).", CONFIRM),
    Feature("phaseshift_max", "Phaseshift max", "cell", "Maximum phase shift within the cell; unit not stated.", CONFIRM),
    Feature("phaseshift_min", "Phaseshift min", "cell", "Minimum phase shift within the cell; unit not stated.", CONFIRM),
    Feature("phaseshift_std", "Phaseshift std. dev.", "cell", "Standard deviation of phase shift within the cell; unit not stated.", CONFIRM),
    Feature("phaseshift_sum", "Phaseshift sum", "cell", "Sum of phase shift over the cell; unit not stated.", CONFIRM),
    Feature("roughness_avg", "Roughness avg", "cell", f"{_SHAPE_NOTE}.", CONFIRM),
    Feature("roughness_kurtosis", "Roughness kurtosis", "cell", f"{_SHAPE_NOTE}.", CONFIRM),
    Feature("roughness_rms", "Roughness RMS", "cell", f"{_SHAPE_NOTE}.", CONFIRM),
    Feature("roughness_skewness", "Roughness skewness", "cell", f"{_SHAPE_NOTE}.", CONFIRM),
    Feature("shape_convexity", "Shape convexity", "cell", f"{_SHAPE_NOTE}; differs from 'Hull convexity' in a way the export does not specify.", CONFIRM, "Shape convexity"),
    Feature("texture_clustershade", "Texture clustershade", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_clustertendency", "Texture clustertendency", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_contrast", "Texture contrast", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_correlation", "Texture correlation", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_correlation_info1", "Texture correlation info1", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_correlation_info2", "Texture correlation info2", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_energy", "Texture energy", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_entropy", "Texture entropy", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_homogeneity", "Texture homogeneity", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
    Feature("texture_maxprob", "Texture maxprob", "cell", "Texture (GLCM-type) statistic; definition not stated in the export.", CONFIRM),
]

# --- frame-level ("Frame stats"): frame data + per-frame averages exported by the software --------
FRAME_ONLY_FEATURES: list[Feature] = [
    Feature("n_cells", "No. Cells", "frame", "Number of (non-edge) cells in the frame.", AS_EXPORTED, "Number of cells"),
    Feature("confluence_pct", "Confluence (%)", "frame", "Percentage of the frame covered by cells (as exported).", AS_EXPORTED, "Confluence"),
]
# Frame-stats columns whose unit differs from the same-named column in 'All cells'
FRAME_PIXEL_FEATURES: list[Feature] = [
    Feature("exp_avg_boxed_center_x_pxl", "Boxed center pos X (pxl)", "frame", "Software-exported frame average; position in pixels.", AS_EXPORTED),
    Feature("exp_avg_boxed_center_y_pxl", "Boxed center pos Y (pxl)", "frame", "Software-exported frame average; position in pixels.", AS_EXPORTED),
    Feature("exp_avg_peak_x_pxl", "Peak pos X (pxl)", "frame", "Software-exported frame average; position in pixels.", AS_EXPORTED),
    Feature("exp_avg_peak_y_pxl", "Peak pos Y (pxl)", "frame", "Software-exported frame average; position in pixels.", AS_EXPORTED),
]
FRAME_AVG_PREFIX = "exp_avg_"

# --- image-level ("Frame info") ----------------------------------------------------------------
IMAGE_FEATURES: list[Feature] = [
    Feature("image_width_pxl", "Image width (pxl)", "image", "Image width in pixels.", AS_EXPORTED),
    Feature("image_height_pxl", "Image height (pxl)", "image", "Image height in pixels.", AS_EXPORTED),
    Feature("scale_um_per_pxl", "Image scale factor (µm/pxl)", "image", "Pixel size; used to interpret µm positions.", AS_EXPORTED),
    Feature("image_width_um", "Image width (µm)", "image", "Image width in µm.", AS_EXPORTED),
    Feature("image_height_um", "Image height (µm)", "image", "Image height in µm.", AS_EXPORTED),
    Feature("image_area_um2", "Image area (µm²)", "image", "Imaged area in µm².", AS_EXPORTED),
    Feature("min_phase_shift_rad", "Min phase shift (rad)", "image", "Minimum phase shift in the image.", AS_EXPORTED),
    Feature("max_phase_shift_rad", "Max phase shift (rad)", "image", "Maximum phase shift in the image.", AS_EXPORTED),
    Feature("total_phase_shift_rad", "Total phase shift (rad)", "image", "Total phase shift in the image (name-based).", CONFIRM),
    Feature("laser_wavelength_nm", "Laser wavelength (nm)", "image", "Laser wavelength.", AS_EXPORTED),
]

# --- tracking: per-cell sheets ('Cell n') and feature-by-time matrices ------------------------
TRACK_FEATURES: list[Feature] = [
    Feature("area_um2", "Area (µm²)", "track_obs", "Cell area at this time point (matches the morphology export for linked cells).", AS_EXPORTED, "Cell area"),
    Feature("irregularity", "Irregularity", "track_obs", "Irregularity at this time point (see morphology dictionary).", CONFIRM),
    Feature("migration_um", "Migration (µm)", "track_obs", "Straight-line distance of 'Relative pos X/Y' from the track origin (founder cell: its own first position; daughter: its parent's last position). Checked arithmetically.", VALIDATED, "Net displacement (exported)"),
    Feature("migration_directness", "Migration directness (migration vs motility)", "track_obs", "Migration / Motility (net displacement ÷ accumulated distance). Checked arithmetically.", VALIDATED, "Directness (exported)"),
    Feature("motility_um", "Motility (µm)", "track_obs", "Accumulated path length. For a daughter it starts at the distance from the parent's last position to its first position (not 0), then adds step lengths. Checked arithmetically.", VALIDATED, "Accumulated distance (exported)"),
    Feature("motility_speed_um_h", "Motility speed (µm/h)", "track_obs", "Mean speed (path length / time), not a per-step instantaneous speed. On the sample it equals the mean over a trailing window (about 2.5 h; the window is inferred from the data, see QC) and the cumulative mean while Age is shorter than the window. Window setting requires confirmation.", CONFIRM, "Windowed mean speed (exported)"),
    Feature("optical_thickness_avg_um", "Optical thickness avg (µm)", "track_obs", "Mean optical thickness at this time point.", AS_EXPORTED),
    Feature("optical_volume_um3", "Optical volume (µm³)", "track_obs", "Optical volume at this time point.", CONFIRM),
    Feature("age_s", "Age (s)", "track_obs", "Time since the track's first observation.", VALIDATED),
    Feature("rel_x_um", "Relative pos X (µm)", "track_obs", "X position relative to the track origin: founder cells start at 0; a daughter is measured from its parent's last position (validated against centroids of linked cells).", VALIDATED),
    Feature("rel_y_um", "Relative pos Y (µm)", "track_obs", "Y position relative to the track origin (see Relative pos X).", VALIDATED),
    Feature("end_status", "End status", "track_obs", "Fate recorded on the last row of a track (e.g. Divides, Migrates out of frame, Disappears, Stays until end).", AS_EXPORTED),
]

# 'Time' in tracking sheets and 'Date time' in morphology sheets are handled structurally.

ALL_FEATURES: list[Feature] = (CELL_FEATURES + FRAME_ONLY_FEATURES + FRAME_PIXEL_FEATURES
                                + IMAGE_FEATURES + TRACK_FEATURES)

CELL_MAP = {f.original: f.internal for f in CELL_FEATURES}
FRAME_MAP = {f.original: f.internal for f in FRAME_ONLY_FEATURES + FRAME_PIXEL_FEATURES}
# frame averages of cell features: same original name, prefixed internal name
FRAME_AVG_MAP = {f.original: FRAME_AVG_PREFIX + f.internal for f in CELL_FEATURES}
IMAGE_MAP = {f.original: f.internal for f in IMAGE_FEATURES}
TRACK_MAP = {f.original: f.internal for f in TRACK_FEATURES}
# 'Averages' sheet columns are 'Avg. <track feature>'
TRACK_AVG_MAP = {"Avg. " + f.original: "avg_" + f.internal for f in TRACK_FEATURES
                 if f.internal not in {"age_s", "rel_x_um", "rel_y_um", "end_status"}}
TRACK_AVG_MAP["No. tracked cells"] = "n_tracked_cells"

# Heuristic plausibility bounds used ONLY to flag records (never to remove them).
EXPECTED_RANGES: dict[str, tuple[float | None, float | None, str]] = {
    "area_um2": (0.0, None, "area must be positive"),
    "perimeter_um": (0.0, None, "perimeter must be positive"),
    "eccentricity": (0.0, 1.0, "eccentricity is expected in [0, 1]"),
    "hull_convexity": (0.0, 1.0, "convexity is expected in (0, 1]"),
    "shape_convexity": (0.0, 1.0, "convexity is expected in (0, 1]"),
    "irregularity": (0.0, 1.0, "irregularity is expected in [0, 1] (heuristic)"),
    "optical_thickness_avg_um": (0.0, None, "optical thickness is expected to be non-negative"),
    "optical_volume_um3": (0.0, None, "optical volume is expected to be non-negative"),
    "confluence_pct": (0.0, 100.0, "confluence is a percentage"),
}

# Feature groups used by the UI.
HEADLINE_CELL_FEATURES = ["area_um2", "perimeter_um", "hull_convexity", "shape_convexity", "irregularity",
                          "eccentricity", "optical_thickness_avg_um", "optical_volume_um3"]

_BY_INTERNAL: dict[str, Feature] = {}
for _f in CELL_FEATURES + FRAME_ONLY_FEATURES + IMAGE_FEATURES:
    _BY_INTERNAL.setdefault(_f.internal, _f)
for _f in TRACK_FEATURES:
    _BY_INTERNAL.setdefault(_f.internal, _f)


def unit_of(internal: str) -> str | None:
    """Unit of a feature as exported (e.g. 'µm²'), or None."""
    f = _BY_INTERNAL.get(internal)
    if f is None and internal.startswith(FRAME_AVG_PREFIX):
        f = _BY_INTERNAL.get(internal[len(FRAME_AVG_PREFIX):])
    return split_name_unit(f.original)[1] if f else None


def label_of(internal: str, with_unit: bool = True) -> str:
    """Human-readable axis label, e.g. 'Cell area (µm²)'."""
    f = _BY_INTERNAL.get(internal)
    if f is None:
        return internal
    base = f.label or split_name_unit(f.original)[0]
    unit = unit_of(internal)
    return f"{base} ({unit})" if (with_unit and unit) else base


def status_of(internal: str) -> str:
    """Interpretation status of a feature: as exported / validated in data / requires confirmation."""
    f = _BY_INTERNAL.get(internal)
    return f.status if f else CONFIRM


