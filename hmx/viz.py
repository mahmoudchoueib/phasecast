"""Plotly figure builders. Restrained, colour-blind-safe styling suitable for screen sharing."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from . import spec

PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#7A7A7A", "#F0E442"]
SEQUENTIAL = "Viridis"
FONT = dict(family="Segoe UI, Helvetica, Arial, sans-serif", size=14, color="#1f2933")
PLOT_CONFIG = {"displaylogo": False, "toImageButtonOptions": {"format": "png", "scale": 2, "filename": "hmx_figure"},
               "modeBarButtonsToRemove": ["lasso2d", "select2d"]}


def style(fig: go.Figure, title: str | None = None, x: str | None = None, y: str | None = None, height: int = 480) -> go.Figure:
    """Apply the common look (white template, fonts, axis titles, legend) to a figure."""
    fig.update_layout(template="plotly_white", font=FONT, height=height, margin=dict(l=70, r=30, t=70 if title else 30, b=60),
                      title=dict(text=title, x=0.01, font=dict(size=18)) if title else None,
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1.0),
                      hoverlabel=dict(font_size=13), colorway=PALETTE)
    fig.update_xaxes(title=x, showline=True, linecolor="#9aa5b1", gridcolor="#e4e7eb", zeroline=False)
    fig.update_yaxes(title=y, showline=True, linecolor="#9aa5b1", gridcolor="#e4e7eb", zeroline=False)
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    """Convert a hex colour to an rgba() string with transparency (for bands)."""
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def fig_timeseries(ts: pd.DataFrame, feature: str, stat: str, band: str, smoothed: bool, show_raw: bool,
                   title: str | None = None) -> go.Figure:
    """Line plot of a binned time series with optional spread band, raw and smoothed lines."""
    fig = go.Figure()
    groups = list(ts["group"].unique())
    for i, g in enumerate(groups):
        d = ts[ts["group"] == g]
        col = PALETTE[i % len(PALETTE)]
        name = str(g) if g != "all" else spec.label_of(feature, False)
        yc = "center_smooth" if smoothed and "center_smooth" in d else "center"
        if band != "none" and d["lo"].notna().any():
            lo = d["lo_smooth"] if smoothed and "lo_smooth" in d else d["lo"]
            hi = d["hi_smooth"] if smoothed and "hi_smooth" in d else d["hi"]
            fig.add_trace(go.Scatter(x=d["bin_h"], y=hi, mode="lines", line=dict(width=0), hoverinfo="skip", showlegend=False))
            fig.add_trace(go.Scatter(x=d["bin_h"], y=lo, mode="lines", line=dict(width=0), fill="tonexty",
                                     fillcolor=_rgba(col, 0.18), hoverinfo="skip", showlegend=False,
                                     name=f"{name} {'±SD' if band == 'sd' else 'IQR'}"))
        if smoothed and show_raw:
            fig.add_trace(go.Scatter(x=d["bin_h"], y=d["center"], mode="lines+markers", name=f"{name} (raw)",
                                     line=dict(color=_rgba(col, 0.35), width=1), marker=dict(size=4, color=_rgba(col, 0.5)),
                                     customdata=d[["n_obs", "n_frames", "n_positions"]],
                                     hovertemplate="%{x:.2f} h<br>%{y:.4g}<br>n obs=%{customdata[0]}, frames=%{customdata[1]}<extra>raw</extra>"))
        fig.add_trace(go.Scatter(x=d["bin_h"], y=d[yc], mode="lines+markers", name=name + (" (smoothed)" if smoothed else ""),
                                 line=dict(color=col, width=2.5), marker=dict(size=5, color=col),
                                 customdata=d[["n_obs", "n_frames", "n_positions"]],
                                 hovertemplate=f"%{{x:.2f}} h<br>{stat} = %{{y:.4g}}<br>n obs=%{{customdata[0]}}, frames=%{{customdata[1]}}, positions=%{{customdata[2]}}<extra></extra>"))
    style(fig, title or f"{spec.label_of(feature, False)} over time", "Elapsed imaging time (h)", spec.label_of(feature))
    return fig


def fig_hist(df: pd.DataFrame, feature: str, color: str | None, bins: int = 40, normalise: bool = False,
             title: str | None = None) -> go.Figure:
    """Overlaid histograms of a feature, one per group (e.g. time window)."""
    fig = px.histogram(df, x=feature, color=color, nbins=bins, barmode="overlay", opacity=0.6,
                       histnorm="probability density" if normalise else None, color_discrete_sequence=PALETTE)
    return style(fig, title or f"Distribution of {spec.label_of(feature, False)}", spec.label_of(feature),
                 "Density" if normalise else "Number of cell observations")


def fig_box(df: pd.DataFrame, feature: str, group: str, title: str | None = None, violin: bool = False) -> go.Figure:
    """Box or violin plot of a feature per group."""
    fn = px.violin if violin else px.box
    kw = dict(box=True, points=False) if violin else dict(points=False)
    fig = fn(df, x=group, y=feature, color=group, color_discrete_sequence=PALETTE, **kw)
    fig.update_layout(showlegend=False)
    return style(fig, title or f"{spec.label_of(feature, False)} by window", "Window", spec.label_of(feature))


def fig_heterogeneity(h: pd.DataFrame, feature: str) -> go.Figure:
    """CV and IQR/median of a feature against time."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=h["bin_h"], y=h["cv"], mode="lines+markers", name="CV (SD / mean)",
                             line=dict(color=PALETTE[0], width=2.5), customdata=h[["n"]],
                             hovertemplate="%{x:.1f} h<br>CV=%{y:.3f}<br>n=%{customdata[0]}<extra></extra>"))
    fig.add_trace(go.Scatter(x=h["bin_h"], y=h["iqr_over_median"], mode="lines+markers", name="IQR / median",
                             line=dict(color=PALETTE[1], width=2.5, dash="dot"), customdata=h[["n"]],
                             hovertemplate="%{x:.1f} h<br>IQR/median=%{y:.3f}<br>n=%{customdata[0]}<extra></extra>"))
    return style(fig, f"Heterogeneity of {spec.label_of(feature, False)} over time", "Elapsed imaging time (h)",
                 "Relative spread (dimensionless)", 400)


def fig_scatter(df: pd.DataFrame, x: str, y: str, color: str | None, trend: bool, hover_cols: list[str],
                max_points: int = 20000, title: str | None = None) -> go.Figure:
    """Scatter of two features with optional colour and OLS trend line (WebGL for large n)."""
    d = df
    if len(d) > max_points:
        d = d.sample(max_points, random_state=0)
    cont = color is not None and pd.api.types.is_numeric_dtype(d[color]) and d[color].nunique() > 12
    kw = dict(color_continuous_scale=SEQUENTIAL) if cont else dict(color_discrete_sequence=PALETTE)
    fig = px.scatter(d, x=x, y=y, color=color, hover_data={c: True for c in hover_cols if c in d and c not in (x, y)},
                     opacity=0.55, render_mode="webgl" if len(d) > 3000 else "auto", **kw)
    fig.update_traces(marker=dict(size=5))
    if trend:
        dd = df[[x, y]].astype("float64").replace([np.inf, -np.inf], np.nan).dropna()
        if len(dd) > 2 and dd[x].nunique() > 1:
            k, b = np.polyfit(dd[x], dd[y], 1)
            xs = np.array([dd[x].min(), dd[x].max()])
            fig.add_trace(go.Scatter(x=xs, y=k * xs + b, mode="lines", name="Linear fit (OLS)",
                                     line=dict(color="#111", dash="dash", width=2)))
    if color and cont:
        fig.update_layout(coloraxis_colorbar=dict(title=spec.label_of(color) if color in spec.CELL_FEATURES else color))
    return style(fig, title or f"{spec.label_of(y, False)} vs {spec.label_of(x, False)}", spec.label_of(x), spec.label_of(y), 520)


def fig_corr_heatmap(c: pd.DataFrame, method: str) -> go.Figure:
    """Heatmap of a correlation matrix."""
    labels = [spec.label_of(i, False) for i in c.index]
    fig = go.Figure(go.Heatmap(z=c.values, x=labels, y=labels, zmin=-1, zmax=1, colorscale="RdBu_r", reversescale=False,
                               colorbar=dict(title=f"{method.title()}"), hovertemplate="%{y}<br>%{x}<br>%{z:.2f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return style(fig, f"{method.title()} correlation (exploratory; repeated observations)", None, None,
                 max(450, 22 * len(labels) + 200))


def fig_trajectories(obs: pd.DataFrame, xcol: str, ycol: str, mode: str, color_by: str = "track",
                     fov: tuple[float, float] | None = None, title: str | None = None) -> go.Figure:
    """obs: observations of the selected tracks. mode: 'origin' (each track starts at 0,0), 'family' or 'field'."""
    fig = go.Figure()
    tracks = list(obs["track_id"].unique())
    if color_by == "time":
        d = obs.dropna(subset=[xcol, ycol])
        fig.add_trace(go.Scatter(x=d[xcol], y=d[ycol], mode="markers", marker=dict(color=d["elapsed_s"] / 3600, colorscale=SEQUENTIAL,
                                 size=5, colorbar=dict(title="Elapsed (h)")), text=d["track_id"],
                                 hovertemplate="%{text}<br>x=%{x:.1f} µm, y=%{y:.1f} µm<br>%{marker.color:.1f} h<extra></extra>",
                                 showlegend=False))
    for i, t in enumerate(tracks):
        d = obs[obs["track_id"] == t].sort_values("elapsed_s").dropna(subset=[xcol, ycol])
        if d.empty:
            continue
        col = PALETTE[i % len(PALETTE)]
        fig.add_trace(go.Scatter(x=d[xcol], y=d[ycol], mode="lines" if color_by == "time" else "lines+markers", name=t,
                                 line=dict(color="#9aa5b1" if color_by == "time" else col, width=1.6), marker=dict(size=4, color=col),
                                 customdata=np.c_[d["elapsed_s"] / 3600], showlegend=color_by != "time" or len(tracks) <= 12,
                                 hovertemplate=t + "<br>x=%{x:.1f} µm, y=%{y:.1f} µm<br>%{customdata[0]:.2f} h<extra></extra>"))
        fig.add_trace(go.Scatter(x=d[xcol].iloc[:1], y=d[ycol].iloc[:1], mode="markers", showlegend=False, hoverinfo="skip",
                                 marker=dict(symbol="circle-open", size=10, color="#111", line=dict(width=2))))
        fig.add_trace(go.Scatter(x=d[xcol].iloc[-1:], y=d[ycol].iloc[-1:], mode="markers", showlegend=False, hoverinfo="skip",
                                 marker=dict(symbol="square", size=8, color="#111")))
    ttl = {"origin": "Trajectories, each track started at the origin", "family": "Trajectories in the family frame (lineage-continuous)",
           "field": "Trajectories in the imaging field"}[mode]
    style(fig, title or ttl, "x (µm)", "y (µm)", 600)
    fig.update_yaxes(scaleanchor="x", scaleratio=1, autorange="reversed" if mode == "field" else True)
    if mode == "field" and fov:
        fig.update_xaxes(range=[0, fov[0]])
        fig.update_yaxes(range=[fov[1], 0], scaleanchor="x", scaleratio=1)
    return fig


def fig_distribution(df: pd.DataFrame, col: str, title: str, xlabel: str, ylabel: str = "Number of tracks", bins: int = 30) -> go.Figure:
    """Simple histogram used on the tracking pages."""
    fig = px.histogram(df, x=col, nbins=bins, color_discrete_sequence=[PALETTE[0]])
    return style(fig, title, xlabel, ylabel, 380)


def fig_to_png(fig: go.Figure, scale: int = 2) -> bytes | None:
    """PNG bytes via kaleido, or None when no image engine / Chrome is available (the modebar camera still works)."""
    try:
        return fig.to_image(format="png", scale=scale, width=1100, height=fig.layout.height or 500)
    except Exception:
        return None
