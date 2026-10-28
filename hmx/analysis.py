"""Descriptive statistics used by the dashboard pages (no inference about biology)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

STAT_FUNCS = {"mean": "mean", "median": "median"}
WINDOW_PRESETS_H = [6, 12, 24, 48, 72]


def describe(x: pd.Series | np.ndarray) -> dict:
    """Summary statistics of a series: n, mean, median, SD, CV, IQR, 10/25/75/90th percentiles, min, max."""
    v = pd.Series(x, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if v.empty:
        return dict(n=0, mean=np.nan, median=np.nan, std=np.nan, cv=np.nan, iqr=np.nan, p10=np.nan, p25=np.nan,
                    p75=np.nan, p90=np.nan, min=np.nan, max=np.nan)
    q = v.quantile([0.10, 0.25, 0.75, 0.90])
    mean, sd = v.mean(), (v.std(ddof=1) if len(v) > 1 else np.nan)
    return dict(n=int(len(v)), mean=mean, median=v.median(), std=sd, cv=(sd / mean if mean not in (0, np.nan) and np.isfinite(sd) else np.nan),
                iqr=q[0.75] - q[0.25], p10=q[0.10], p25=q[0.25], p75=q[0.75], p90=q[0.90], min=v.min(), max=v.max())


def bin_hours(elapsed_h: pd.Series, width_h: float) -> pd.Series:
    """Round elapsed hours to the nearest time-bin centre."""
    return (elapsed_h / width_h).round() * width_h


def auto_bin_width_h(frames: pd.DataFrame) -> float:
    """Median acquisition interval in hours, rounded to 0.25 h (0.5 h for the sample data)."""
    if frames.empty or "elapsed_h" not in frames:
        return 0.5
    d = frames.sort_values(["sample_id", "elapsed_h"]).groupby("sample_id", observed=True)["elapsed_h"].diff().dropna()
    if d.empty:
        return 0.5
    return float(max(0.25, round(d.median() * 4) / 4))


def timeseries(cells: pd.DataFrame, frames: pd.DataFrame, feature: str, level: str, stat: str = "mean",
               band: str = "none", weighting: str = "frame", bin_h: float = 0.5,
               t_range: tuple[float, float] | None = None, group_by: str | None = None,
               smooth_window: int = 1) -> pd.DataFrame:
    """Time series of `feature` against elapsed time.

    level='cell'  : feature is a per-cell measurement (cells table).
        weighting='frame'  : statistic per frame first, then averaged over frames in the bin (each frame counts equally).
        weighting='pooled' : statistic over all cell observations in the bin (frames with more cells count more).
        band='sd'  : mean +- SD  (frame mode: the average within-frame SD; pooled: SD of pooled cells)
        band='iqr' : 25th-75th percentile (frame mode: average of per-frame quartiles; pooled: pooled quartiles)
    level='frame' : feature is one value per frame (frames table); statistic across the frames in the bin.
    Returns columns: group, bin_h, center, lo, hi, n_obs, n_frames, n_positions.
    """
    src = cells if level == "cell" else frames
    if src.empty or feature not in src:
        return pd.DataFrame(columns=["group", "bin_h", "center", "lo", "hi", "n_obs", "n_frames", "n_positions"])
    d = src.copy()
    d[feature] = d[feature].astype("float64")
    if t_range:
        d = d[(d["elapsed_h"] >= t_range[0]) & (d["elapsed_h"] <= t_range[1])]
    d["bin_h"] = bin_hours(d["elapsed_h"], bin_h)
    d["group"] = d[group_by].astype(str) if group_by and group_by in d else "all"
    stat_fn = "median" if stat == "median" else "mean"
    rows = []
    for gname, gd in d.groupby("group", observed=True):
        if level == "frame":
            agg = gd.groupby("bin_h")[feature]
            out = pd.DataFrame({"center": getattr(agg, stat_fn)(), "sd": agg.std(ddof=1),
                                "q25": agg.quantile(.25), "q75": agg.quantile(.75), "n_obs": agg.count()})
            out["n_frames"] = gd.groupby("bin_h").apply(lambda x: x[["sample_id", "frame_no"]].drop_duplicates().shape[0], include_groups=False)
        else:
            frame_keys = ["sample_id", "frame_no"]
            if weighting == "pooled":
                agg = gd.groupby("bin_h")[feature]
                out = pd.DataFrame({"center": getattr(agg, stat_fn)(), "sd": agg.std(ddof=1),
                                    "q25": agg.quantile(.25), "q75": agg.quantile(.75), "n_obs": agg.count()})
                out["n_frames"] = gd.groupby("bin_h").apply(lambda x: x[frame_keys].drop_duplicates().shape[0], include_groups=False)
            else:
                fa = gd.groupby(frame_keys + ["bin_h"], observed=True)[feature].agg(
                    f_center=stat_fn, f_sd=lambda s: s.std(ddof=1), f_q25=lambda s: s.quantile(.25),
                    f_q75=lambda s: s.quantile(.75), f_n="count").reset_index()
                g2 = fa.groupby("bin_h")
                out = pd.DataFrame({"center": getattr(g2["f_center"], stat_fn)(), "sd": g2["f_sd"].mean(),
                                    "q25": g2["f_q25"].mean(), "q75": g2["f_q75"].mean(), "n_obs": g2["f_n"].sum(),
                                    "n_frames": g2.size()})
        out["n_positions"] = gd.groupby("bin_h")["sample_id"].nunique()
        out = out.reset_index()
        if band == "sd":
            out["lo"], out["hi"] = out["center"] - out["sd"], out["center"] + out["sd"]
        elif band == "iqr":
            out["lo"], out["hi"] = out["q25"], out["q75"]
        else:
            out["lo"] = out["hi"] = np.nan
        out["group"] = gname
        rows.append(out[["group", "bin_h", "center", "lo", "hi", "n_obs", "n_frames", "n_positions"]])
    res = pd.concat(rows, ignore_index=True).sort_values(["group", "bin_h"])
    if smooth_window and smooth_window > 1:
        for c in ("center", "lo", "hi"):
            res[c + "_smooth"] = res.groupby("group")[c].transform(lambda s: s.rolling(smooth_window, center=True, min_periods=1).mean())
    return res


def window_frame(cells: pd.DataFrame, start_h: float, end_h: float) -> pd.DataFrame:
    """Rows whose elapsed time lies inside [start_h, end_h]."""
    return cells[(cells["elapsed_h"] >= start_h) & (cells["elapsed_h"] <= end_h)]


def compare_windows(a: pd.Series, b: pd.Series) -> dict:
    """Descriptive two-window comparison. Effect sizes only: observations are repeated measures of the
    same cells / positions, so p-values from independence-based tests would be misleading and are not given."""
    a, b = a.dropna().astype("float64"), b.dropna().astype("float64")
    if len(a) < 2 or len(b) < 2:
        return {}
    u = sps.mannwhitneyu(a, b, alternative="two-sided").statistic
    return dict(n_a=len(a), n_b=len(b), median_diff_b_minus_a=float(b.median() - a.median()),
                mean_diff_b_minus_a=float(b.mean() - a.mean()),
                ks_D=float(sps.ks_2samp(a, b).statistic),
                cliffs_delta_b_vs_a=float(2 * (1 - u / (len(a) * len(b))) - 1))  # >0: values in B tend to be larger


def heterogeneity(cells: pd.DataFrame, feature: str, bin_h: float = 1.0, t_range: tuple[float, float] | None = None) -> pd.DataFrame:
    """Within-bin spread of a cell feature over time: SD, CV, IQR / median (pooled cells in each bin)."""
    d = cells[["elapsed_h", feature]].dropna().copy()
    if t_range:
        d = d[(d["elapsed_h"] >= t_range[0]) & (d["elapsed_h"] <= t_range[1])]
    d["bin_h"] = bin_hours(d["elapsed_h"], bin_h)
    rows = []
    for b, g in d.groupby("bin_h"):
        s = describe(g[feature])
        rows.append(dict(bin_h=b, n=s["n"], mean=s["mean"], median=s["median"], sd=s["std"], cv=s["cv"],
                         iqr=s["iqr"], iqr_over_median=(s["iqr"] / s["median"] if s["median"] else np.nan)))
    return pd.DataFrame(rows)


def linear_trend(x: pd.Series, y: pd.Series) -> dict:
    """Slope/intercept of y on x (descriptive; no p-value is reported)."""
    d = pd.concat([x, y], axis=1).dropna().astype("float64")
    if len(d) < 3 or d.iloc[:, 0].nunique() < 2:
        return dict(slope=np.nan, intercept=np.nan, r=np.nan, n=len(d))
    r = sps.linregress(d.iloc[:, 0], d.iloc[:, 1])
    return dict(slope=float(r.slope), intercept=float(r.intercept), r=float(r.rvalue), n=len(d))


def correlate(df: pd.DataFrame, x: str, y: str) -> dict:
    """Pearson r and Spearman rho between two columns (n = complete pairs)."""
    d = df[[x, y]].astype("float64").replace([np.inf, -np.inf], np.nan).dropna()
    if len(d) < 3 or d[x].nunique() < 2 or d[y].nunique() < 2:
        return dict(n=len(d), pearson_r=np.nan, spearman_rho=np.nan)
    return dict(n=len(d), pearson_r=float(sps.pearsonr(d[x], d[y]).statistic),
                spearman_rho=float(sps.spearmanr(d[x], d[y]).statistic))


def corr_matrix(df: pd.DataFrame, features: list[str], method: str = "spearman") -> pd.DataFrame:
    """Correlation matrix of several features."""
    return df[features].astype("float64").replace([np.inf, -np.inf], np.nan).corr(method=method)

