"""
GENERAL MODEL CONFIGURATION  v2.0
Agrobit s.r.l.

This file collects all user-editable settings.
DON'T modify the other files directly — all tuning belongs here.
"""

from pathlib import Path

# FILE PATHS
RULES_EXCEL      = Path("db_crops_PDM.xlsx")
PEST_CONFIG_JSON = Path("pest.json")
WEATHER_EXCEL    = Path("weather_data.xlsx")
OUTPUT_DIR       = Path("output")

# WEATHER DATA FORMAT
WEATHER_COLUMNS = {
    "date":     "date",
    "temp_max": "temp_max",
    "temp_min": "temp_min",
    "humidity": "humidity",
    "rainfall": "rainfall",
}
# Date format: "%Y-%m-%d" | "%d-%m-%Y" | "%d/%m/%Y" — auto-detected if set to "auto"
DATE_FORMAT = "auto"

# HEMISPHERE & SEASONAL SETTINGS
# Month when GDD accumulation resets (1=Jan for NH, 7=Jul for SH)
# Tropics: leave at 1 (irrelevant when pheno window is [0, 9999])
GDD_RESET_MONTH = 1

# PHENOLOGICAL WINDOW VARIABILITY
# Fraction of the window used as soft transition zone at each edge.
# Covers year-to-year variation: ±50-100 GDD5 (~±7-14 days) documented by
# Linkosalo et al. (2008) Int. J. Biometeorol. 52:341-347 and
# Menzel et al. (2006) Global Change Biol. 12:1969-1976.
# 0.12 covers approx. ±1.5 SD of interannual phenological variation.
PHENO_FUZZY_MARGIN_FRAC = 0.12

# FUZZY MODEL PARAMETERS
FUZZY_TRANSITION_FRACTION = 0.15   # edge softness of T/RH membership functions
FUZZY_SPAN_CAP            = 30.0   # max effective span for sentinel-bounded rules (°C or %RH)
                                   # prevents T=[-999,5] from generating transition zones of 150°C
FUZZY_MIN_MU              = 0.01   # minimum activation to count a rule

# RISK CLASSIFICATION THRESHOLDS (score 0-100)
#  0-29   Low       unfavourable, no action
# 30-64   Moderate  partially favourable, increase monitoring
# 65-87   High      strongly favourable, intervention recommended within 48-72h
# 88-100  Critical  all factors optimal, immediate action required
RISK_THRESHOLD_CRITICAL = 88
RISK_THRESHOLD_HIGH     = 65
RISK_THRESHOLD_MODERATE = 30

# Minimum score factor applied when streak requirement not met
STREAK_MIN_FACTOR = 0.30

# GROWING DEGREE DAYS
# Global phenological proxy T_base (pest-specific t_base are in pest.json)
PHENOLOGY_T_BASE = 5.0

# All distinct t_base values used across pests (derived from pest.json).
# compute_features() pre-computes gdd_cum_{X}b columns for each value so that
# score_day() can look up the phenologically correct GDD for every pest.
PEST_T_BASES = {0.0, 4.0, 5.0, 8.0, 9.0, 10.0, 12.0, 13.0, 14.0, 15.0, 18.0}

GDD_CHART_BASES = {
    "Fungi (0 C)":      0.0,
    "Pathogens (5 C)":  5.0,
    "Insects (10 C)":   10.0,
}

# LEAF WETNESS ESTIMATION (daily RH -> estimated hours of wetness per day)
# Based on Pedro & Gillespie (1982) Agric. Meteorol. 25:283-296;
# Sentelhas et al. (2008) Agric. For. Meteorol. 148:1002-1017
WETNESS_HOURS_BY_RH = [
    (95, 20),  # RH >= 95%  -> 20 h/day
    (85, 14),  # RH >= 85%  -> 14 h/day
    (75,  8),  # RH >= 75%  ->  8 h/day
    (65,  4),  # RH >= 65%  ->  4 h/day
    ( 0,  0),
]
# Additional hours from rainfall events (additive, capped at 24h)
WETNESS_HOURS_BY_RAIN = [
    (5.0, 6),
    (1.0, 4),
    (0.1, 2),
    (0.0, 0),
]

# REPORT SETTINGS
REPORT_TITLE    = "Pest & Disease Risk Report"
REPORT_SUBTITLE = "Agrobit iAgro Platform — Multi-crop Fuzzy Risk Model v2.0"
REPORT_FILENAME = "pest_risk_report.pdf"

REPORT_SECTIONS = {
    "cover":            True,
    "weather_overview": True,
    "gdd":              True,
    "risk_timeline":    True,
    "seasonal_heatmap": False,
    "distribution":     False,
}

# COLOUR PALETTE
COLORS = {
    "critical":   "#7B1FA2",   # purple  - Critical
    "high":       "#B71C1C",   # dark red - High
    "moderate":   "#F9A825",   # amber   - Moderate
    "low":        "#2E7D32",   # green   - Low
    "oos":        "#B0BEC5",   # grey    - Out of season
    "temp":       "#E65100",
    "humidity":   "#1565C0",
    "rainfall":   "#90CAF9",
    "gdd_base0":  "#00695C",
    "gdd_base5":  "#1565C0",
    "gdd_base10": "#E65100",
    "accent":     "#1F6E3E",
}
