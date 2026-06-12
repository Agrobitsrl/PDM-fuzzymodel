"""
CHARTS & REPORT  v2.0
Agrobit S.r.l.

Functions
  weather_overview()   Temperature, humidity, precipitation panels
  gdd_chart()          Cumulative + daily GDD curves
  risk_timeline()      Risk score per pest over time (one page per crop)
  build_report()       Assemble all sections into a PDF
"""

import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from pathlib import Path

from config import (
    COLORS,
    REPORT_TITLE, REPORT_SUBTITLE, REPORT_SECTIONS,
    GDD_CHART_BASES,
    RISK_THRESHOLD_CRITICAL,
    RISK_THRESHOLD_HIGH,
    RISK_THRESHOLD_MODERATE,
)

plt.rcParams.update({
    'font.family':       'DejaVu Sans',
    'axes.spines.top':   False,
    'axes.spines.right': False,
    'axes.facecolor':    '#FAFAFA',
    'figure.facecolor':  'white',
    'axes.grid':         True,
    'grid.color':        '#CFD8DC',
    'grid.linewidth':    0.5,
    'axes.labelsize':    10,
    'axes.titlesize':    12,
    'axes.titleweight':  'bold',
    'xtick.labelsize':   9,
    'ytick.labelsize':   9,
})


def _shorten(name: str, n: int = 42) -> str:
    return name if len(name) <= n else name[:n-1] + '…'


def _month_fmt(ax, rotation: int = 0):
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %y'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=rotation, ha='right')


# ─── COVER PAGE ───────────────────────────────────────────────────────────────
def page_cover(pdf: PdfPages, df: pd.DataFrame, df_weather: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(11.7, 8.3))
    accent = COLORS['accent']
    ax.set_facecolor(accent)
    fig.patch.set_facecolor(accent)
    ax.axis('off')

    crops  = ', '.join(sorted(df['crop'].unique()))
    period = (f"{str(df['date'].min())[:10]}  →  {str(df['date'].max())[:10]}")
    n_crit = (df['risk_class'] == 'Critical').sum()
    n_high = (df['risk_class'] == 'High').sum()

    ax.text(0.5, 0.86, REPORT_TITLE, color='white', ha='center',
            transform=ax.transAxes, fontsize=26, fontweight='bold')
    ax.text(0.5, 0.77, REPORT_SUBTITLE, color='#A5D6A7', ha='center',
            transform=ax.transAxes, fontsize=13)
    ax.text(0.5, 0.68, period, color='white', ha='center',
            transform=ax.transAxes, fontsize=12)

    stats = [
        (str(df['crop'].nunique()),  'Crops'),
        (str(df['pest'].nunique()),  'Pests'),
        (str(df['date'].nunique()),  'Days'),
        (str(n_crit),                'Critical Events'),
        (str(n_high),                'High Events'),
    ]
    for i, (val, lbl) in enumerate(stats):
        x = 0.10 + i * 0.20
        rect = mpatches.FancyBboxPatch(
            (x - 0.08, 0.30), 0.16, 0.22,
            boxstyle='round,pad=0.01', linewidth=0,
            facecolor='#1B5E20', transform=ax.transAxes
        )
        ax.add_patch(rect)
        color_val = '#EF9A9A' if lbl == 'Critical Events' else 'white'
        ax.text(x, 0.43, val, color=color_val, ha='center',
                transform=ax.transAxes, fontsize=18, fontweight='bold')
        ax.text(x, 0.33, lbl, color='#A5D6A7', ha='center',
                transform=ax.transAxes, fontsize=8)

    ax.text(0.5, 0.19, crops, color='#C8E6C9', ha='center',
            transform=ax.transAxes, fontsize=10)

    # Risk level legend
    legend_items = [
        (COLORS['critical'], 'Critical (≥88)'),
        (COLORS['high'],     'High (65-87)'),
        (COLORS['moderate'], 'Moderate (30-64)'),
        (COLORS['low'],      'Low (<30)'),
    ]
    for i, (color, label) in enumerate(legend_items):
        x = 0.15 + i * 0.175
        rect2 = mpatches.FancyBboxPatch(
            (x - 0.06, 0.09), 0.12, 0.05,
            boxstyle='round,pad=0.005', linewidth=0,
            facecolor=color, alpha=0.85, transform=ax.transAxes
        )
        ax.add_patch(rect2)
        ax.text(x, 0.115, label, color='white', ha='center',
                transform=ax.transAxes, fontsize=7, fontweight='bold')

    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ─── WEATHER OVERVIEW ─────────────────────────────────────────────────────────
def weather_overview(pdf: PdfPages, df_weather: pd.DataFrame):
    fig = plt.figure(figsize=(11.7, 8.3))
    fig.suptitle('Weather Overview', fontsize=14, fontweight='bold', y=0.99)
    gs = gridspec.GridSpec(3, 1, figure=fig, hspace=0.55)

    df = df_weather.copy()
    df['temp_avg'] = (df['temp_max'] + df['temp_min']) / 2
    dates = pd.to_datetime(df['date'])

    # Temperature
    ax1 = fig.add_subplot(gs[0])
    ax1.fill_between(dates, df['temp_min'], df['temp_max'],
                     alpha=0.20, color=COLORS['temp'], label='Range min–max')
    ax1.plot(dates, df['temp_avg'].rolling(7, min_periods=1).mean(),
             color=COLORS['temp'], lw=2.0, label='7-day moving avg')
    ax1.plot(dates, df['temp_avg'], color=COLORS['temp'], lw=0.5, alpha=0.35)
    ax1.set_ylabel('Temperature (°C)')
    ax1.set_title('Temperature')
    ax1.legend(fontsize=8, loc='upper right', framealpha=0.8)
    _month_fmt(ax1)

    # Humidity
    ax2 = fig.add_subplot(gs[1])
    ax2.fill_between(dates, df['humidity'], alpha=0.15, color=COLORS['humidity'])
    ax2.plot(dates, df['humidity'].rolling(7, min_periods=1).mean(),
             color=COLORS['humidity'], lw=2.0, label='7-day moving avg')
    ax2.axhline(70, color='#FFA000', lw=1.0, ls='--', alpha=0.7, label='70% threshold')
    ax2.axhline(85, color=COLORS['high'], lw=1.0, ls='--', alpha=0.7, label='85% threshold')
    ax2.set_ylabel('Humidity (%)'); ax2.set_ylim(0, 110)
    ax2.set_title('Relative Humidity')
    ax2.legend(fontsize=8, loc='upper right', framealpha=0.8)
    _month_fmt(ax2)

    # Rainfall + 10-day cumulate
    ax3 = fig.add_subplot(gs[2])
    ax3.bar(dates, df['rainfall'], color=COLORS['rainfall'], width=1.0, label='Daily rainfall')
    ax3b = ax3.twinx()
    rain_10d = df['rainfall'].rolling(10, min_periods=1).sum()
    ax3b.plot(dates, rain_10d, color=COLORS['humidity'], lw=1.8, label='10-day cumulate')
    ax3b.set_ylabel('10-day cumulate (mm)', color=COLORS['humidity'])
    ax3b.tick_params(axis='y', labelcolor=COLORS['humidity'])
    ax3b.spines['right'].set_visible(True)
    ax3b.spines['right'].set_color(COLORS['humidity'])
    ax3.set_ylabel('Rainfall (mm/day)')
    ax3.set_title('Rainfall')
    _month_fmt(ax3)
    lines1, lbl1 = ax3.get_legend_handles_labels()
    lines2, lbl2 = ax3b.get_legend_handles_labels()
    ax3.legend(lines1 + lines2, lbl1 + lbl2, fontsize=8, loc='upper right', framealpha=0.8)

    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ─── GDD CHART ────────────────────────────────────────────────────────────────
def gdd_chart(pdf: PdfPages, df_weather: pd.DataFrame):
    df = df_weather.copy()
    df['date']     = pd.to_datetime(df['date'])
    df['temp_avg'] = (df['temp_max'] + df['temp_min']) / 2
    df['doy']      = df['date'].dt.dayofyear
    df['year']     = df['date'].dt.year

    fig, axes = plt.subplots(1, 2, figsize=(11.7, 5.5))
    fig.suptitle('Growing Degree Days (GDD)', fontsize=14, fontweight='bold', y=1.01)

    base_colors = [COLORS['gdd_base0'], COLORS['gdd_base5'], COLORS['gdd_base10']]
    base_items  = list(GDD_CHART_BASES.items())
    yr_last     = df['year'].max()

    # Left: cumulative GDD per year
    ax = axes[0]
    for (label, tbase), color in zip(base_items, base_colors):
        for yr, grp in df.groupby('year'):
            gdd_cum = np.maximum(0, grp['temp_avg'] - tbase).cumsum().values
            doys    = grp['doy'].values
            is_last = (yr == yr_last)
            ax.plot(doys, gdd_cum, color=color,
                    lw=2.2 if is_last else 0.8,
                    alpha=1.0 if is_last else 0.25,
                    label=f'{label} ({yr})' if is_last else None)

    month_starts = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
    month_labels = ['Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec']
    ax.set_xticks(month_starts)
    ax.set_xticklabels(month_labels, rotation=45, ha='right', fontsize=8)
    ax.set_xlabel('Day of year'); ax.set_ylabel('Cumulative GDD')
    ax.set_title('Cumulative GDD per T_base (all years)')
    ax.legend(fontsize=8)

    # Right: daily GDD with 14-day MA for most recent year
    ax2 = axes[1]
    dfl = df[df['year'] == yr_last].copy()
    for (label, tbase), color in zip(base_items, base_colors):
        gdd_d = np.maximum(0, dfl['temp_avg'] - tbase)
        gdd_r = gdd_d.rolling(14, min_periods=1).mean()
        ax2.plot(dfl['date'], gdd_r, color=color, lw=2.0, label=label)
        ax2.fill_between(dfl['date'], gdd_r, alpha=0.08, color=color)
    ax2.set_title(f'Daily GDD (14-day MA) — {yr_last}')
    ax2.set_ylabel('GDD/day')
    ax2.legend(fontsize=8)
    _month_fmt(ax2, rotation=30)

    fig.tight_layout()
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ─── RISK TIMELINE ────────────────────────────────────────────────────────────
def risk_timeline(pdf: PdfPages, df_results: pd.DataFrame, crop: str):
    """
    One page per crop; one panel per pest.
    4-colour risk bands: purple=Critical, red=High, amber=Moderate, green=Low.
    Out-of-season periods are excluded (score=0 for those days).
    """
    dfc = df_results[
        (df_results['crop'] == crop) &
        (df_results['risk_class'] != 'Out of season')
    ].copy()
    dfc['date'] = pd.to_datetime(dfc['date'])

    pests = sorted(dfc['pest'].unique())
    n     = len(pests)
    if n == 0:
        return

    height = max(4.5, n * 2.0)
    fig, axes = plt.subplots(n, 1, figsize=(11.7, height), sharex=True)
    if n == 1:
        axes = [axes]

    fig.suptitle(f'{crop}  —  Risk Score Timeline',
                 fontsize=13, fontweight='bold', y=1.01)

    yticks     = [0, RISK_THRESHOLD_MODERATE, RISK_THRESHOLD_HIGH,
                  RISK_THRESHOLD_CRITICAL, 100]
    yticklabels = ['0', str(RISK_THRESHOLD_MODERATE), str(RISK_THRESHOLD_HIGH),
                   str(RISK_THRESHOLD_CRITICAL), '100']

    for ax, pest in zip(axes, pests):
        dfp    = dfc[dfc['pest'] == pest].sort_values('date')
        dates  = dfp['date'].values
        scores = dfp['risk_score'].values

        # Coloured fill areas per risk band (bottom to top: Low, Moderate, High, Critical)
        ax.fill_between(dates, scores,
                        where=(scores < RISK_THRESHOLD_MODERATE),
                        color=COLORS['low'],      alpha=0.60)
        ax.fill_between(dates, scores,
                        where=((scores >= RISK_THRESHOLD_MODERATE) &
                               (scores <  RISK_THRESHOLD_HIGH)),
                        color=COLORS['moderate'], alpha=0.75)
        ax.fill_between(dates, scores,
                        where=((scores >= RISK_THRESHOLD_HIGH) &
                               (scores <  RISK_THRESHOLD_CRITICAL)),
                        color=COLORS['high'],     alpha=0.80)
        ax.fill_between(dates, scores,
                        where=(scores >= RISK_THRESHOLD_CRITICAL),
                        color=COLORS['critical'], alpha=0.85)

        ax.plot(dates, scores, color='#37474F', lw=0.6, alpha=0.5)

        # Threshold reference lines
        ax.axhline(RISK_THRESHOLD_MODERATE,
                   color=COLORS['moderate'], lw=0.8, ls='--', alpha=0.50)
        ax.axhline(RISK_THRESHOLD_HIGH,
                   color=COLORS['high'],     lw=0.8, ls='--', alpha=0.50)
        ax.axhline(RISK_THRESHOLD_CRITICAL,
                   color=COLORS['critical'], lw=0.8, ls='--', alpha=0.50)

        ax.set_ylim(0, 108)
        ax.set_yticks(yticks)
        ax.set_yticklabels(yticklabels, fontsize=7)
        ax.set_ylabel('Score', fontsize=8)
        ax.set_title(_shorten(pest, 50), fontsize=9, loc='left', pad=2)

        # Compact stats annotation
        in_crit = (scores >= RISK_THRESHOLD_CRITICAL).sum()
        in_high = ((scores >= RISK_THRESHOLD_HIGH) & (scores < RISK_THRESHOLD_CRITICAL)).sum()
        ax.text(0.99, 0.92,
                f'Critical: {in_crit}d  High: {in_high}d',
                transform=ax.transAxes, ha='right', va='top',
                fontsize=7.5, color='#37474F',
                bbox=dict(boxstyle='round,pad=0.2', fc='white', alpha=0.7))

    _month_fmt(axes[-1], rotation=30)

    # Unified legend at bottom
    handles = [
        mpatches.Patch(color=COLORS['critical'], label=f'Critical ≥{RISK_THRESHOLD_CRITICAL}'),
        mpatches.Patch(color=COLORS['high'],     label=f'High {RISK_THRESHOLD_HIGH}–{RISK_THRESHOLD_CRITICAL-1}'),
        mpatches.Patch(color=COLORS['moderate'], label=f'Moderate {RISK_THRESHOLD_MODERATE}–{RISK_THRESHOLD_HIGH-1}'),
        mpatches.Patch(color=COLORS['low'],      label=f'Low <{RISK_THRESHOLD_MODERATE}'),
    ]
    fig.legend(handles=handles, loc='lower center', ncol=4,
               fontsize=9, frameon=False, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=[0, 0.04, 1, 1])

    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ─── BUILD REPORT ─────────────────────────────────────────────────────────────
def build_report(
    df_results:  pd.DataFrame,
    df_weather:  pd.DataFrame,
    output_path: Path,
):
    """Assemble all enabled report sections into a single PDF."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    crops = sorted(df_results['crop'].unique())

    with PdfPages(str(output_path)) as pdf:
        if REPORT_SECTIONS.get('cover', True):
            print("  -> Cover page")
            page_cover(pdf, df_results, df_weather)

        if REPORT_SECTIONS.get('weather_overview', True):
            print("  -> Weather overview")
            weather_overview(pdf, df_weather)

        if REPORT_SECTIONS.get('gdd', True):
            print("  -> GDD chart")
            gdd_chart(pdf, df_weather)

        if REPORT_SECTIONS.get('risk_timeline', True):
            for crop in crops:
                print(f"  -> Risk timeline: {crop}")
                risk_timeline(pdf, df_results, crop)

        d = pdf.infodict()
        d['Title']   = REPORT_TITLE
        d['Subject'] = 'Agrobit iAgro Fuzzy Risk Model v2.0'

    print(f"\n[OK] Report saved: {output_path}")
