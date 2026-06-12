"""
FUZZY PEST & DISEASE RISK MODEL  v2.0
Agrobit S.r.l.

PIPELINE
  STEP 1  load_rules()       – parse fuzzy rules from Excel
          load_pest_config() – parse biological parameters from JSON
          load_weather()     – parse and normalise weather data

  STEP 2  compute_features() – derived weather features
            GDD accumulation respecting GDD_RESET_MONTH (hemisphere-aware)
            Improved leaf wetness estimation (RH + rainfall combined)
            Streak counters for multiple humidity thresholds
            Multi-window moving averages and rainfall cumulates

  STEP 3  Membership functions
            _trapezoid()             – core trapezoidal μ function
            membership_humidity()
            membership_temp()
            membership_rainfall()
            membership_phenology()   – NEW: fuzzy phenological window
                                       accounts for year-to-year variability

  STEP 4  score_day()  – per-day, per-pest Mamdani fuzzy inference
            1. Fuzzy phenological gating (replaces binary in/out)
            2. Weather input selection (fungi: 7d MA; insects: daily)
            3. Fuzzification of T, RH, rain for every rule
            4. Mamdani AND aggregation (min)
            5. Weighted defuzzification
            6. Streak penalty
            7. Phenological multiplier applied

  STEP 5  run_model() – iterate over all (day × crop × pest) combinations
"""

import json
import re
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    FUZZY_TRANSITION_FRACTION,
    FUZZY_SPAN_CAP,
    FUZZY_MIN_MU,
    RISK_THRESHOLD_CRITICAL,
    RISK_THRESHOLD_HIGH,
    RISK_THRESHOLD_MODERATE,
    STREAK_MIN_FACTOR,
    PHENOLOGY_T_BASE,
    PEST_T_BASES,
    GDD_RESET_MONTH,
    PHENO_FUZZY_MARGIN_FRAC,
    WEATHER_COLUMNS,
    DATE_FORMAT,
    WETNESS_HOURS_BY_RH,
    WETNESS_HOURS_BY_RAIN,
)

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

_RISK_SCORE_MAP = {
    "Critical": 100,
    "High":      80,
    "Moderate":  40,
    "Medium":    40,
    "Low":       10,
}

# Pathogen genera whose behaviour is better modelled with 7-day moving averages
# (slow environmental response typical of fungi and bacteria)
_PATHOGEN_KEYWORDS = {
    "Venturia", "Plasmopora", "Plasmopara", "Uncinula", "Erysiphe",
    "Botrytis", "Monilia", "Monilinia", "Taphrina",
    "Guignardia", "Phomopsis", "Colletotrichum", "Pseudomonas",
    "Pseudocercospora", "Alternaria", "Hemileia", "Phytophthora",
    "Moniliophthora",
    # NOTE: "Erwinia" intentionally excluded — fire blight is episodic (1-3 day
    # events driven by daily T spikes), not a sustained-wetness pathogen.
    # Using daily temperature (not 7-day MA) captures infection windows correctly.
}


def _parse_humidity(raw: str) -> tuple[float, float]:
    """Parse a humidity string to a numeric (lo, hi) range."""
    h = str(raw).strip().replace('%', '').replace(' ', '')
    if not h or h.lower() == 'whatever':
        return (0.0, 100.0)
    if h.startswith('>='):
        return (float(h[2:]), 100.0)
    if h.startswith('>'):
        return (float(h[1:]), 100.0)
    if h.startswith('<='):
        return (0.0, float(h[2:]))
    if h.startswith('<'):
        return (0.0, float(h[1:]))
    if '-' in h:
        parts = h.split('-')
        if len(parts) == 2:
            try:
                return (float(parts[0]), float(parts[1]))
            except ValueError:
                pass
    try:
        v = float(h)
        return (v, v)
    except ValueError:
        return (0.0, 100.0)


def _parse_temp(raw: str) -> tuple[float, float]:
    """
    Parse a temperature string to a numeric (lo, hi) range.

    Handles all formats found in the PDM database:
      Double-bound: "20<=T<=25", "25<T<=30", "5<=T<20", "10<T<30"
      Single lower: "T>=10", "T>30", ">30"
      Single upper: "T<=30", "T<5",  "<5"
      Whatever:     "whatever", empty
    """
    raw_clean = str(raw).strip().replace(' ', '')
    if not raw_clean or raw_clean.lower() == 'whatever':
        return (-999.0, 999.0)

    t = raw_clean.upper()

    # ── Case 1: explicit double-bound WITH T in the middle ────────────────
    # Patterns: "LO<=T<=HI", "LO<T<=HI", "LO<=T<HI", "LO<T<HI"
    m = re.match(
        r'^([-\d.]+)\s*[<>]=?\s*T\s*[<>]=?\s*([-\d.]+)$',
        t, re.IGNORECASE
    )
    if m:
        return (float(m.group(1)), float(m.group(2)))

    # ── Case 2: T removed — try single-bound and exact forms ─────────────
    t2 = t.replace('T', '').strip()

    if t2.startswith('>='):
        return (float(t2[2:]), 999.0)
    if t2.startswith('>'):
        return (float(t2[1:]), 999.0)
    if t2.startswith('<='):
        return (-999.0, float(t2[2:]))
    if t2.startswith('<'):
        return (-999.0, float(t2[1:]))

    # ── Case 3: exact numeric value ──────────────────────────────────────
    try:
        v = float(t2)
        return (v, v)
    except ValueError:
        return (-999.0, 999.0)


def _parse_rainfall(raw: str) -> float:
    """Parse a rainfall threshold string to a minimum mm value."""
    r = str(raw).strip().lower()
    if not r or r == 'whatever' or r == 'nan':
        return 0.0
    r = r.replace('mm', '').replace('<', '').replace('>', '').replace('=', '').strip()
    try:
        return max(0.0, float(r))
    except ValueError:
        return 0.0


def load_rules(xlsx_path: Path) -> list[dict]:
    """
    Load the fuzzy rules Excel file.

    Returns a list of dicts with keys:
      crop, pest, pest_key, hum_lo, hum_hi, temp_lo, temp_hi,
      rain_min, risk, risk_score

    Crop aliases normalise legacy names in the Excel file so that
    pest.json, the PDF report, and the output CSV all use the same label.
    """
    # Normalise legacy Excel crop names to the canonical names used in
    # pest.json and the report (add entries here if other aliases exist).
    CROP_ALIASES: dict[str, str] = {
        # 'Vineyard' → 'Grape' alias removed: pest.json now uses 'Vineyard'
        # as the canonical key, matching the Excel crop label directly.
    }

    df = pd.read_excel(xlsx_path)
    # Accept any column order; map by position
    df.columns = ['crop', 'pest', 'humidity', 'temp', 'rainfall', 'risk', 'type']

    rules = []
    for _, row in df.iterrows():
        if pd.isna(row['crop']):
            continue

        risk_str  = str(row['risk']).strip()
        pest_str  = str(row['pest']).strip()
        # Base pest_key: scientific name before first comma.
        # e.g. "Taphrina deformans, Peach leaf curl (fruits)" → "Taphrina deformans"
        pest_key  = pest_str.split(',')[0].strip()
        # If the full pest name ends with a parenthetical variant like "(fruits)" or
        # "(shoots)", append it to pest_key so it matches the pest.json key exactly.
        # e.g. pest_key becomes "Taphrina deformans (fruits)" to match pest.json.
        _variant_match = re.search(r'(\([^)]+\))\s*$', pest_str)
        if _variant_match:
            _variant = _variant_match.group(1)
            # Only append if not already present in pest_key (avoid duplicates)
            if _variant not in pest_key:
                pest_key = f'{pest_key} {_variant}'
        crop_raw  = str(row['crop']).strip()
        crop_name = CROP_ALIASES.get(crop_raw, crop_raw)  # apply alias

        hum_lo, hum_hi   = _parse_humidity(str(row['humidity']))
        temp_lo, temp_hi = _parse_temp(str(row['temp']))
        rain_min         = _parse_rainfall(str(row['rainfall']))

        rules.append({
            'crop':       crop_name,
            'pest':       pest_str,
            'pest_key':   pest_key,
            'hum_lo':     hum_lo,
            'hum_hi':     hum_hi,
            'temp_lo':    temp_lo,
            'temp_hi':    temp_hi,
            'rain_min':   rain_min,
            'risk':       risk_str,
            'risk_score': _RISK_SCORE_MAP.get(risk_str, 10),
        })

    return rules


def load_pest_config(json_path: Path) -> dict:
    """
    Load biological parameters per (crop, pest_key) from JSON.

    Returns a dict keyed by (crop, pest_key) with fields:
      t_base, min_streak, pheno_lo, pheno_hi, pheno_frac_lo, pheno_frac_hi,
      t_optimal_min, t_optimal_max (optional)
    """
    with open(json_path, encoding='utf-8') as f:
        raw = json.load(f)

    config = {}
    for crop, pests in raw.items():
        if not isinstance(pests, dict):
            continue   # skip _metadata or other non-pest keys
        for pest_key, params in pests.items():
            if not isinstance(params, dict):
                continue
            # t_lethal_max: support both field names (t_lethal_max and the
            # older t_lethal_infection_max used for Taphrina).
            t_lethal_max = (
                params.get('t_lethal_max')
                or params.get('t_lethal_infection_max')
            )
            # pheno_t_base: optional override for the GDD base used ONLY for
            # phenological gating. Decouples the pathogen activity threshold
            # (t_base, e.g. 18°C for Erwinia) from the HOST phenology GDD
            # (pheno_t_base=5°C tracks pear flowering in March-April, not
            # heat accumulation above 18°C which peaks in summer).
            pheno_t_base = params.get('pheno_t_base', params.get('t_base', 10.0))
            # rain_window_days: 1 = use daily rainfall, 3 = use rain_3d (default).
            # Set to 1 for pathogens where the infection rule refers to a 24-h
            # rain event (e.g. Plasmopora viticola Goidanich rule: >=10 mm/24h).
            rain_window_days = int(params.get('rain_window_days', 3))

            config[(crop, pest_key)] = {
                't_base':          params.get('t_base', 10.0),
                'pheno_t_base':    pheno_t_base,
                'rain_window_days': rain_window_days,
                'min_streak':      params.get('min_streak', 1),
                'pheno_lo':        params.get('phenology_window_gdd', [None, None])[0],
                'pheno_hi':        params.get('phenology_window_gdd', [None, None])[1],
                'pheno_frac_lo':   params.get('pheno_fraction', [None, None])[0],
                'pheno_frac_hi':   params.get('pheno_fraction', [None, None])[1],
                't_optimal_min':   params.get('t_optimal_min', None),
                't_optimal_max':   params.get('t_optimal_max', None),
                # Lethal temperature bounds
                't_lethal_min':    params.get('t_lethal_min', None),
                't_lethal_max':    t_lethal_max,
                # Mills-type wetness-hours gate
                'min_wetness_hours_critical': params.get('min_wetness_hours_critical', None),
                'min_wetness_hours_high':     params.get('min_wetness_hours_high', None),
            }
    return config


def _detect_date_format(series: pd.Series) -> str:
    """Auto-detect date format from sample values."""
    sample = str(series.dropna().iloc[0])
    if re.match(r'^\d{4}-\d{2}-\d{2}', sample):
        return '%Y-%m-%d'
    if re.match(r'^\d{2}-\d{2}-\d{4}', sample):
        return '%d-%m-%Y'
    if re.match(r'^\d{2}/\d{2}/\d{4}', sample):
        return '%d/%m/%Y'
    if re.match(r'^\d{4}/\d{2}/\d{2}', sample):
        return '%Y/%m/%d'
    return '%Y-%m-%d'


def load_weather(excel_path: Path) -> pd.DataFrame:
    """
    Load daily weather data, normalise column names, handle missing values.

    Accepts .xlsx and .xls; auto-detects date format when DATE_FORMAT='auto'.
    """
    path = Path(excel_path)
    if path.suffix.lower() == '.xls':
        df = pd.read_excel(path, engine='xlrd')
    else:
        df = pd.read_excel(path)

    col_map = {v: k for k, v in WEATHER_COLUMNS.items()}
    df = df.rename(columns=col_map)

    # Required columns check
    for col in ['date', 'temp_max', 'temp_min', 'humidity', 'rainfall']:
        if col not in df.columns:
            raise ValueError(
                f"Missing column '{col}' in weather file. "
                f"Available: {list(df.columns)}"
            )

    # Date parsing
    fmt = DATE_FORMAT if DATE_FORMAT != 'auto' else _detect_date_format(df['date'])
    df['date'] = pd.to_datetime(df['date'], format=fmt, dayfirst=True, errors='coerce')
    df = df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)

    # Interpolate temperature; forward-fill humidity and rainfall
    for col in ['temp_max', 'temp_min']:
        df[col] = pd.to_numeric(df[col], errors='coerce').interpolate('linear')
    df['humidity'] = (
        pd.to_numeric(df['humidity'], errors='coerce').ffill().fillna(70.0)
    )
    df['rainfall'] = (
        pd.to_numeric(df['rainfall'], errors='coerce').fillna(0.0).clip(lower=0)
    )

    return df[['date', 'temp_max', 'temp_min', 'humidity', 'rainfall']].copy()


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — FEATURE ENGINEERING
# ─────────────────────────────────────────────────────────────────────────────

def _wetness_hours(rh_series: pd.Series, rain_series: pd.Series) -> pd.Series:
    """
    Estimate daily leaf-wetness hours from relative humidity and rainfall.

    Combines two independent contributions:
      1. RH-based hours (Pedro & Gillespie 1982; Sentelhas et al. 2008)
      2. Rain-based additional hours (additive)
    Capped at 24 h/day.
    """
    rh   = rh_series.values
    rain = rain_series.values
    h_rh = np.zeros(len(rh))
    for thr, hrs in sorted(WETNESS_HOURS_BY_RH, key=lambda x: x[0]):
        h_rh = np.where(rh >= thr, hrs, h_rh)

    h_rain = np.zeros(len(rain))
    for thr, hrs in sorted(WETNESS_HOURS_BY_RAIN, key=lambda x: x[0]):
        h_rain = np.where(rain >= thr, hrs, h_rain)

    return pd.Series(np.minimum(24.0, h_rh + h_rain), index=rh_series.index)


def _gdd_season_key(date_series: pd.Series) -> pd.Series:
    """
    Compute the 'season year' label for each date, respecting GDD_RESET_MONTH.

    Northern Hemisphere (reset month=1): season_year = calendar year
    Southern Hemisphere (reset month=7):
      dates in Jul-Dec → season_year = calendar year
      dates in Jan-Jun → season_year = calendar year - 1
    This ensures cumulative GDD resets on the first day of GDD_RESET_MONTH.
    """
    years  = date_series.dt.year
    months = date_series.dt.month
    if GDD_RESET_MONTH == 1:
        return years
    # For any other reset month: seasons span two calendar years
    return np.where(months >= GDD_RESET_MONTH, years, years - 1)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add derived weather features to the daily weather DataFrame.

    New columns
    ───────────────────────────────────────────────────────────────────────
    temp_avg            (Tmax + Tmin) / 2

    temp_avg_{N}d       rolling mean temperature (N = 3, 7, 14 days)
    humidity_{N}d       rolling mean humidity
    rainfall_{N}d       rolling sum rainfall

    rain_3d / rain_10d  3- and 10-day cumulative rainfall

    wetness_h           estimated leaf-wetness hours per day (RH + rain)
    wetness_3d          3-day rolling sum of wetness_h

    gdd_daily_{tag}     daily GDD for bases 0°C, 5°C, 10°C, phenology
    gdd_cum_{tag}       cumulative GDD from start of season
                        (respects GDD_RESET_MONTH for SH compatibility)
    gdd_annual_ref      estimated full-season GDD5 (for pheno_fraction)

    streak_hum{thr}     consecutive days with humidity >= thr (60,70,80,85,90,95)
    ───────────────────────────────────────────────────────────────────────
    """
    df = df.copy().sort_values('date').reset_index(drop=True)
    df['temp_avg'] = (df['temp_max'] + df['temp_min']) / 2.0

    # ── Rolling averages and cumulates ──────────────────────────────────
    for w in [3, 7, 14]:
        df[f'temp_avg_{w}d'] = df['temp_avg'].rolling(w, min_periods=1).mean()
        df[f'humidity_{w}d'] = df['humidity'].rolling(w, min_periods=1).mean()
        df[f'rainfall_{w}d'] = df['rainfall'].rolling(w, min_periods=1).sum()

    df['rain_3d']  = df['rainfall_3d']
    df['rain_10d'] = df['rainfall'].rolling(10, min_periods=1).sum()

    # ── Leaf wetness ─────────────────────────────────────────────────────
    df['wetness_h']  = _wetness_hours(df['humidity'], df['rainfall'])
    df['wetness_3d'] = df['wetness_h'].rolling(3, min_periods=1).sum()

    # ── Season key (hemisphere-aware) ────────────────────────────────────
    df['season_year'] = _gdd_season_key(pd.to_datetime(df['date']))

    # ── GDD accumulation — standard bases + all pest-specific bases ───────
    # Compute GDD for every unique t_base used by pests (from PEST_T_BASES).
    # Columns: gdd_daily_{X}b  and  gdd_cum_{X}b  where X = int(t_base).
    # This allows score_day() to use each pest's own thermal threshold for
    # phenological window gating (improvement 3.1.2).
    _all_bases = sorted(PEST_T_BASES | {0.0, 5.0, 10.0})
    for t_base in _all_bases:
        label = f'{int(t_base)}b'
        if f'gdd_cum_{label}' not in df.columns:
            daily = np.maximum(0.0, df['temp_avg'] - t_base)
            df[f'gdd_daily_{label}'] = daily
            df[f'gdd_cum_{label}']   = df.groupby('season_year')[f'gdd_daily_{label}'].transform('cumsum')

    # Phenological proxy GDD (kept for backward-compatibility)
    t_p = PHENOLOGY_T_BASE
    df['gdd_daily_pheno'] = np.maximum(0.0, df['temp_avg'] - t_p)
    df['gdd_cum_pheno']   = df.groupby('season_year')['gdd_daily_pheno'].transform('cumsum')

    # ── Per-base annual GDD reference (for pheno_fraction normalisation) ──
    # gdd_annual_ref_{X}b: mean annual cumulative GDD for each t_base.
    # Used in score_day() to convert pheno_frac → absolute GDD for the pest's
    # own thermal model, making the phenological window climate-independent.
    for t_base in _all_bases:
        label  = f'{int(t_base)}b'
        col    = f'gdd_daily_{label}'
        seasons_b = df.groupby('season_year')[col].agg(count='count', total='sum')
        complete_b = seasons_b[seasons_b['count'] >= 350]
        if len(complete_b) > 0:
            ref_b = float(complete_b['total'].mean())
        else:
            last_b = seasons_b.iloc[-1]
            ref_b = float(last_b['total']) * (365.0 / max(int(last_b['count']), 1))
        df[f'gdd_annual_ref_{label}'] = ref_b

    # Backward-compatible global annual ref (uses 5b base)
    df['gdd_annual_ref'] = df['gdd_annual_ref_5b']

    # ── Vapour Pressure Deficit (Tetens formula, kPa) ─────────────────────
    # VPD = (1 − RH/100) × e_sat ;  e_sat = 0.6108 × exp(17.27T/(T+237.3))
    # VPD < 0.43 kPa → optimal fungal pathogen conditions (Grossiord et al.
    # 2020 New Phytol.; Novick et al. 2024 Plant Cell Environ.)
    _e_sat = 0.6108 * np.exp(17.27 * df['temp_avg'] / (df['temp_avg'] + 237.3))
    df['vpd']    = ((1.0 - df['humidity'] / 100.0) * _e_sat).clip(lower=0.0)
    df['vpd_3d'] = df['vpd'].rolling(3, min_periods=1).mean()
    df['vpd_7d'] = df['vpd'].rolling(7, min_periods=1).mean()

    # ── Humidity streaks ──────────────────────────────────────────────────
    for thr in [60, 70, 80, 85, 90, 95]:
        count, streak = 0, []
        for h in df['humidity']:
            count = count + 1 if h >= thr else 0
            streak.append(count)
        df[f'streak_hum{thr}'] = streak

    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — FUZZY MEMBERSHIP FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _trapezoid(x: float, a: float, b: float, c: float, d: float) -> float:
    """
    Trapezoidal membership function.

      μ = 0    for x <= a or x >= d
      μ = 1    for b <= x <= c
      μ = linear ramp on [a,b] and [c,d]
    """
    if x <= a or x >= d:
        return 0.0
    if b <= x <= c:
        return 1.0
    if a < x < b:
        return (x - a) / (b - a)
    return (d - x) / (d - c)


def membership_humidity(h: float, lo: float, hi: float) -> float:
    """Fuzzy membership of humidity in range [lo, hi].

    FUZZY_SPAN_CAP prevents sentinel-bounded rules (e.g. RH=[0,75]) from
    generating excessively wide transition zones that fire at unintended values.
    """
    if lo == 0.0 and hi == 100.0:
        return 1.0
    raw_width      = hi - lo
    eff_width      = min(max(raw_width, 5.0), FUZZY_SPAN_CAP)
    transition     = eff_width * FUZZY_TRANSITION_FRACTION
    return _trapezoid(h, lo - transition, lo, hi, hi + transition)


def membership_temp(t: float, lo: float, hi: float) -> float:
    """
    Fuzzy membership of temperature in range [lo, hi].

    For 'whatever' ranges (-999 to 999), always returns 1.0.
    Minimum transition width 1.5°C prevents over-sharp boundaries.
    FUZZY_SPAN_CAP (30°C) prevents sentinel-bounded rules such as T=[-999, 5]
    from producing transition zones of 150°C that fire spuriously at 20°C.
    """
    if lo <= -900 and hi >= 900:
        return 1.0
    raw_width  = hi - lo
    eff_width  = min(max(raw_width, 2.0), FUZZY_SPAN_CAP)
    transition = max(eff_width * FUZZY_TRANSITION_FRACTION, 1.5)
    return _trapezoid(t, lo - transition, lo, hi, hi + transition)


def membership_rainfall(r_cum_3d: float, rain_min: float) -> float:
    """
    Fuzzy membership of 3-day cumulative rainfall against minimum threshold.

    Uses 3-day cumulate because most pathogens integrate rainfall over time.
    Above 6× threshold, membership decreases (excess water can inhibit spores).
    """
    if rain_min == 0.0:
        return 1.0
    return _trapezoid(r_cum_3d,
                      rain_min * 0.4,
                      rain_min,
                      rain_min * 3,
                      rain_min * 6)


def membership_phenology(
    gdd_now: float,
    lo: float,
    hi: float,
) -> float:
    """
    Fuzzy phenological window membership.

    Unlike the previous binary in/out check, this returns a continuous
    membership value in [0, 1] with soft transitions at the window edges.

    This accounts for interannual phenological variability (±7-14 days,
    equivalent to ±50-100 GDD5 — Linkosalo et al. 2008; Menzel et al. 2006).

    Transition margin = PHENO_FUZZY_MARGIN_FRAC × (hi - lo).
    """
    width  = hi - lo
    margin = width * PHENO_FUZZY_MARGIN_FRAC

    # Fully inside window
    if lo <= gdd_now <= hi:
        return 1.0
    # Ramp-up zone: just before season start
    if (lo - margin) < gdd_now < lo:
        return (gdd_now - (lo - margin)) / margin
    # Ramp-down zone: just after season end
    if hi < gdd_now < (hi + margin):
        return ((hi + margin) - gdd_now) / margin
    # Outside all zones
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — PER-DAY SCORING
# ─────────────────────────────────────────────────────────────────────────────

def score_day(
    weather_row:      pd.Series,
    rules_crop_pest:  list[dict],
    pest_key:         str,
    pest_params:      dict,
) -> dict:
    """
    Compute the risk score for one (crop, pest) pair on a single day.

    Pipeline
    ────────────────────────────────────────────────────────────────────
    1. PHENOLOGICAL GATING (fuzzy)
       Compute the phenological multiplier μ_pheno ∈ [0,1].
       If μ_pheno = 0: return Out of season (no computation needed).
       If μ_pheno < 1: score is scaled proportionally (partial season).
       This replaces the old binary hard cutoff.

    2. WEATHER INPUT SELECTION
       Fungi/bacteria: 7-day moving average (slow environmental response)
       Insects/mites:  current-day values (fast thermal response)

    3. FUZZIFICATION
       For each rule, compute μ_T, μ_RH, μ_rain via trapezoidal functions.

    4. MAMDANI AND AGGREGATION
       μ_rule = min(μ_T, μ_RH, μ_rain)

    5. WEIGHTED DEFUZZIFICATION
       score = Σ(μ_i × score_i) / Σ(μ_i)

    6. STREAK PENALTY
       If consecutive favourable days < min_streak → multiply score by
       max(STREAK_MIN_FACTOR, streak / min_streak).

    7. PHENOLOGICAL SCALING
       final_score = score × μ_pheno

    8. CLASSIFICATION
       Critical / High / Moderate / Low based on configured thresholds.
    ────────────────────────────────────────────────────────────────────
    """

    # ── 1. Phenological gating ───────────────────────────────────────────
    # Check plant susceptibility window FIRST (higher priority than lethal-T):
    # if the host is not in the susceptible stage, risk is "Out of season"
    # regardless of current temperature — the tissue targeted by the pest/
    # pathogen simply does not exist yet or has already hardened off.
    pheno_lo   = pest_params.get('pheno_lo')
    pheno_hi   = pest_params.get('pheno_hi')
    frac_lo    = pest_params.get('pheno_frac_lo')
    frac_hi    = pest_params.get('pheno_frac_hi')

    # Phenological gating uses pheno_t_base (may differ from the pathogen's
    # t_base).  For example Erwinia amylovora has t_base=18°C (Cougarblight
    # threshold) but pear flowering tracks GDD_base5, so pheno_t_base=5.
    # Default: pheno_t_base == t_base (backward-compatible).
    pheno_t_base = pest_params.get('pheno_t_base', pest_params.get('t_base', PHENOLOGY_T_BASE))
    gdd_col_pest = f'gdd_cum_{int(pheno_t_base)}b'
    ref_col_pest = f'gdd_annual_ref_{int(pheno_t_base)}b'
    gdd_now = float(weather_row.get(gdd_col_pest, weather_row.get('gdd_cum_pheno', 0)))
    gdd_ref = float(weather_row.get(ref_col_pest, weather_row.get('gdd_annual_ref', 0)))

    # Prefer normalised fraction window (works across all climates)
    if frac_lo is not None and frac_hi is not None and gdd_ref and gdd_ref > 0:
        lo_pheno = frac_lo * gdd_ref
        hi_pheno = frac_hi * gdd_ref
    elif pheno_lo is not None and pheno_hi is not None:
        lo_pheno = pheno_lo
        hi_pheno = pheno_hi
    else:
        lo_pheno = hi_pheno = None

    if lo_pheno is not None and hi_pheno is not None:
        # Wide-open window = always in season
        if hi_pheno >= 9000:
            mu_pheno = 1.0
        else:
            mu_pheno = membership_phenology(gdd_now, lo_pheno, hi_pheno)
        if mu_pheno == 0.0:
            return {
                'score':      0.0,
                'risk_class': 'Out of season',
                'detail':     f'GDD={gdd_now:.0f} outside [{lo_pheno:.0f},{hi_pheno:.0f}]',
            }
    else:
        mu_pheno = 1.0

    # ── 0. Lethal temperature guard (improvement 3.1.3) ─────────────────
    # If daily temperature is outside the organism's survival range, suppress
    # risk immediately — no fuzzy inference needed.
    # (Runs after phenological gating so "Out of season" is reported when
    # both conditions coincide, giving the more informative classification.)
    t_raw = float(weather_row['temp_avg'])
    t_lethal_max_p = pest_params.get('t_lethal_max')
    t_lethal_min_p = pest_params.get('t_lethal_min')
    if t_lethal_max_p is not None and t_raw > float(t_lethal_max_p):
        return {
            'score':      0.0,
            'risk_class': 'Low',
            'detail':     f'T={t_raw:.1f}°C > t_lethal_max={t_lethal_max_p}°C — organism suppressed',
        }
    if t_lethal_min_p is not None and t_raw < float(t_lethal_min_p):
        return {
            'score':      0.0,
            'risk_class': 'Low',
            'detail':     f'T={t_raw:.1f}°C < t_lethal_min={t_lethal_min_p}°C — organism suppressed',
        }

    # ── 2. Weather input selection ────────────────────────────────────────
    is_pathogen = any(kw in pest_key for kw in _PATHOGEN_KEYWORDS)
    # rain_window_days: 1 = daily rainfall, 3 (default) = 3-day cumulative.
    # Pathogens like Plasmopora viticola use the Goidanich rule (>=10 mm/24h)
    # so rain_window_days=1 selects the daily column for a more accurate match.
    rain_window = pest_params.get('rain_window_days', 3)
    if rain_window == 1:
        rain_feature = weather_row['rainfall']
    else:
        rain_feature = weather_row.get('rain_3d', weather_row['rainfall'])

    if is_pathogen:
        t_eff  = weather_row.get('temp_avg_7d', weather_row['temp_avg'])
        h_eff  = weather_row.get('humidity_7d', weather_row['humidity'])
        r_eff  = rain_feature
        streak_col = 'streak_hum70'
    else:
        t_eff  = weather_row['temp_avg']
        h_eff  = weather_row['humidity']
        r_eff  = rain_feature
        streak_col = 'streak_hum60'

    streak = int(weather_row.get(streak_col, 0))

    # ── 3 & 4. Fuzzification + Mamdani aggregation ────────────────────────
    activated = []
    for rule in rules_crop_pest:
        mu_t = membership_temp(t_eff, rule['temp_lo'], rule['temp_hi'])
        mu_h = membership_humidity(h_eff, rule['hum_lo'], rule['hum_hi'])
        mu_r = membership_rainfall(r_eff, rule['rain_min'])

        mu = min(mu_t, mu_h, mu_r)   # Mamdani AND

        if mu > FUZZY_MIN_MU:
            activated.append((mu, rule['risk_score'], rule['risk']))

    if not activated:
        return {'score': 0.0, 'risk_class': 'Low', 'detail': 'no active rules'}

    # ── 5. Weighted defuzzification ───────────────────────────────────────
    total_mu = sum(m for m, _, _ in activated)
    score    = sum(m * s for m, s, _ in activated) / total_mu

    # ── 6. Streak penalty (improvement 3.1.4) ─────────────────────────────
    # For pests with min_streak >= 2 (obligate multi-day wetness requirement,
    # e.g. Taphrina deformans: 2 consecutive days ≥ 12.5h wetness), use a
    # hard floor of 0 — a single isolated favourable day gives zero score.
    # For min_streak = 1 (most insects), keep the STREAK_MIN_FACTOR = 0.30
    # soft floor to avoid sharp cutoffs near the threshold.
    min_streak = pest_params.get('min_streak', 1)
    if streak < min_streak:
        if min_streak >= 2:
            streak_factor = streak / max(min_streak, 1)   # no floor: hard biological requirement
        else:
            streak_factor = max(STREAK_MIN_FACTOR, streak / max(min_streak, 1))
        score *= streak_factor

    # ── 6b. Leaf-wetness hours gate — Mills-type (improvement 3.1.1) ──────
    # If pest.json specifies minimum wetness hours for a risk level, cap the
    # score when the estimated daily wetness falls short.
    # This wires the previously unused min_wetness_hours_critical/high fields.
    # References: Mills (1944) for Venturia; Willocquet & Clerjeau (1998) for
    # Botrytis; Gottwald & Bertrand (1983) for Taphrina.
    wh = float(weather_row.get('wetness_h', 0))
    min_wh_crit = pest_params.get('min_wetness_hours_critical')
    min_wh_high = pest_params.get('min_wetness_hours_high')

    if (min_wh_crit is not None
            and isinstance(min_wh_crit, (int, float))
            and score >= RISK_THRESHOLD_CRITICAL
            and wh < float(min_wh_crit)):
        # Insufficient wetness for Critical: cap just below Critical threshold
        score = min(score, float(RISK_THRESHOLD_CRITICAL) - 0.1)

    if (min_wh_high is not None
            and isinstance(min_wh_high, (int, float))
            and score >= RISK_THRESHOLD_HIGH
            and wh < float(min_wh_high)):
        # Insufficient wetness for High: cap just below High threshold
        score = min(score, float(RISK_THRESHOLD_HIGH) - 0.1)

    # ── 7. Phenological scaling ───────────────────────────────────────────
    score = min(100.0, round(score * mu_pheno, 1))

    # ── 8. Classification ─────────────────────────────────────────────────
    if score >= RISK_THRESHOLD_CRITICAL:
        risk_class = 'Critical'
    elif score >= RISK_THRESHOLD_HIGH:
        risk_class = 'High'
    elif score >= RISK_THRESHOLD_MODERATE:
        risk_class = 'Moderate'
    else:
        risk_class = 'Low'

    best = max(activated, key=lambda x: x[0])
    return {
        'score':      score,
        'risk_class': risk_class,
        'detail':     (f'best_rule={best[2]} mu={best[0]:.2f} '
                       f'mu_pheno={mu_pheno:.2f} streak={streak}d wh={wh:.0f}h'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — MAIN MODEL RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_model(
    rules:        list[dict],
    pest_config:  dict,
    weather_df:   pd.DataFrame,
    filter_crop:  str | None = None,
) -> pd.DataFrame:
    """
    Run the fuzzy risk model over all days and all (crop, pest) pairs.

    Parameters
    ──────────
    rules        : list of rules from load_rules()
    pest_config  : dict from load_pest_config()
    weather_df   : raw weather DataFrame from load_weather()
    filter_crop  : if given, process only that crop

    Returns
    ───────
    DataFrame in long format with one row per (date, crop, pest).
    Key columns: date, crop, pest, risk_score, risk_class, detail
    + weather snapshot columns for interpretability.
    """
    # Add derived features
    weather_df = compute_features(weather_df)

    # Index rules by (crop, pest, pest_key)
    cp_index: dict[tuple, list] = defaultdict(list)
    for rule in rules:
        if filter_crop and rule['crop'].lower() != filter_crop.lower():
            continue
        key = (rule['crop'], rule['pest'], rule['pest_key'])
        cp_index[key].append(rule)

    if not cp_index:
        raise ValueError(
            f"No rules found for crop='{filter_crop}'. "
            "Check spelling against the Excel rules file."
        )

    # Default biological parameters for unknown pests
    _default_params = {
        't_base': 10.0, 'min_streak': 1,
        'pheno_lo': None, 'pheno_hi': None,
        'pheno_frac_lo': None, 'pheno_frac_hi': None,
    }

    results = []
    for (crop, pest, pest_key), rules_cp in cp_index.items():
        # Look up biological parameters
        params = pest_config.get((crop, pest_key))
        if params is None:
            # Fallback: match by pest_key only (ignore crop)
            params = next(
                (v for (c, k), v in pest_config.items() if k == pest_key),
                _default_params,
            )

        for _, wrow in weather_df.iterrows():
            res = score_day(wrow, rules_cp, pest_key, params)

            results.append({
                'date':         wrow['date'],
                'crop':         crop,
                'pest':         pest,
                # Weather snapshot
                'temp_max':     round(float(wrow['temp_max']), 1),
                'temp_min':     round(float(wrow['temp_min']), 1),
                'temp_avg':     round(float(wrow['temp_avg']), 1),
                'humidity':     round(float(wrow['humidity']), 1),
                'rainfall':     round(float(wrow['rainfall']), 1),
                # Key model features
                'temp_7d_avg':  round(float(wrow.get('temp_avg_7d', wrow['temp_avg'])), 1),
                'hum_7d_avg':   round(float(wrow.get('humidity_7d', wrow['humidity'])), 1),
                'rain_3d_sum':  round(float(wrow.get('rain_3d', wrow['rainfall'])), 1),
                'wetness_h':    round(float(wrow.get('wetness_h', 0)), 1),
                'wetness_3d':   round(float(wrow.get('wetness_3d', 0)), 1),
                'gdd_cum_5b':   round(float(wrow.get('gdd_cum_5b', 0)), 0),
                'gdd_cum_pheno':round(float(wrow.get('gdd_cum_pheno', 0)), 0),
                'streak_hum70': int(wrow.get('streak_hum70', 0)),
                'vpd':          round(float(wrow.get('vpd', 0.0)), 2),
                # Output
                'risk_score':   res['score'],
                'risk_class':   res['risk_class'],
                'detail':       res['detail'],
            })

    return pd.DataFrame(results)
