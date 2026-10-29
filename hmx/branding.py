"""PhaseCast visual identity: logo, colours, page header and shared CSS.

Idea behind the mark: a *phase wave* (what holographic microscopy measures) that carries on past a "now"
marker as a dashed, widening cone - the *forecast* of later cell potency from early measurements.
"""
from __future__ import annotations

import streamlit as st

NAVY = "#0B1F3A"
TEAL = "#0F766E"
CYAN = "#22B8CF"
AMBER = "#F59E0B"
SLATE = "#475569"
MIST = "#F1F5F9"

# 12 donor colours: Okabe-Ito-based colour-blind-friendly hues (no amber, which marks flagged wells).
DONOR_PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#7B3F99", "#8C564B", "#4B5563",
                 "#7F8C0A", "#E7298A", "#0E7490", "#9D174D"]      # no amber/orange-yellow: amber is reserved for "flagged" rings
UNRESOLVED_COLOUR = "#94A3B8"      # grey: donor not known
UNASSIGNED_COLOUR = "#CBD5E1"
EMPTY_WELL_COLOUR = "#E2E8F0"

LOGO_SVG = f"""
<svg viewBox="0 0 260 64" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="PhaseCast">
  <defs>
    <linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="{CYAN}"/><stop offset="1" stop-color="{TEAL}"/></linearGradient>
  </defs>
  <!-- measured phase wave -->
  <path d="M4 36 C 12 8, 22 8, 30 36 S 48 64, 56 36 S 70 14, 76 30" fill="none" stroke="url(#g)" stroke-width="4" stroke-linecap="round"/>
  <!-- 'now' marker -->
  <line x1="76" y1="10" x2="76" y2="58" stroke="#CBD5E1" stroke-width="1.5" stroke-dasharray="3 3"/>
  <circle cx="76" cy="30" r="4.5" fill="{AMBER}"/>
  <!-- forecast cone -->
  <path d="M80 30 L132 8 M80 30 L132 52" stroke="{AMBER}" stroke-width="2" stroke-dasharray="4 4" stroke-linecap="round" fill="none"/>
  <path d="M80 30 C 98 24, 112 26, 130 14" fill="none" stroke="{AMBER}" stroke-width="3" stroke-linecap="round" stroke-dasharray="1 7"/>
  <text x="142" y="30" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="25" font-weight="700" fill="#FFFFFF">Phase<tspan fill="{CYAN}">Cast</tspan></text>
  <text x="143" y="48" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="9.5" letter-spacing="1.2" fill="#94A3B8">FORECASTING CELL POTENCY</text>
</svg>
"""

_CSS = f"""
<style>
.block-container {{padding-top: 1.4rem; max-width: 1500px;}}
[data-testid="stMetricValue"] {{font-size: 1.55rem; color: {NAVY};}}
[data-testid="stMetricLabel"] {{color: {SLATE};}}
h1, h2, h3 {{color: {NAVY};}}
.pc-brand {{background: linear-gradient(135deg, {NAVY} 0%, #123B63 100%); border-radius: 12px; padding: 10px 12px 4px 12px; margin-bottom: 10px;}}
.pc-hero {{background: linear-gradient(120deg, {NAVY} 0%, #14507A 60%, {TEAL} 100%); color: #fff; border-radius: 14px;
          padding: 22px 28px; margin-bottom: 18px;}}
.pc-hero h1 {{color: #fff; font-size: 2.0rem !important; margin: 0 0 4px 0;}}
.pc-hero p {{color: #D6E4F0; margin: 0; font-size: 1.02rem;}}
.pc-chip {{display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600; margin-right: 6px;}}
.pc-ok {{background:#DCFCE7; color:#166534;}} .pc-warn {{background:#FEF3C7; color:#92400E;}}
.pc-bad {{background:#FEE2E2; color:#991B1B;}} .pc-info {{background:#E0F2FE; color:#075985;}}
.pc-card {{border: 1px solid #E2E8F0; border-radius: 12px; padding: 14px 16px; background: #fff; height: 100%;}}
.pc-card h4 {{margin: 0 0 4px 0; color: {NAVY}; font-size: 1.0rem;}}
.pc-card p {{margin: 0; color: {SLATE}; font-size: 0.9rem;}}
.pc-step {{border-left: 4px solid {CYAN}; padding-left: 12px; margin-bottom: 10px;}}
.pc-note {{border-left: 4px solid {AMBER}; background: #FFFBEB; padding: 8px 12px; border-radius: 6px; font-size: 0.92rem;}}
</style>
"""


def inject_css() -> None:
    """Add the PhaseCast stylesheet to the page (call once per run)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def sidebar_brand() -> None:
    """Logo banner at the top of the sidebar."""
    st.markdown(f'<div class="pc-brand">{LOGO_SVG}</div>', unsafe_allow_html=True)


def hero(title: str, subtitle: str) -> None:
    """Coloured page banner (used on the landing page and the Plate Mapper)."""
    st.markdown(f'<div class="pc-hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)


def chip(text: str, kind: str = "info") -> str:
    """HTML for a small coloured status label. kind: ok | warn | bad | info."""
    return f'<span class="pc-chip pc-{kind}">{text}</span>'


def donor_colour_map(donor_ids: list[str]) -> dict[str, str]:
    """Stable colour per donor id: sorted order -> palette index (so a donor keeps its colour on every page)."""
    return {d: DONOR_PALETTE[i % len(DONOR_PALETTE)] for i, d in enumerate(sorted(set(donor_ids)))}
