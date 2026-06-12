"""
MAIN  v2.0
Agrobit S.r.l.

Usage:
  python main.py                       # interactive crop selection
  python main.py --crop Cherry         # run for a single crop
  python main.py --crop Cherry Grape   # run for multiple crops
  python main.py --all                 # run for all crops
  python main.py --no-report           # skip PDF generation
  python main.py --validate            # print validation statistics
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from config import (
    RULES_EXCEL, PEST_CONFIG_JSON, WEATHER_EXCEL,
    OUTPUT_DIR, REPORT_FILENAME,
    GDD_RESET_MONTH,
    RISK_THRESHOLD_CRITICAL, RISK_THRESHOLD_HIGH, RISK_THRESHOLD_MODERATE,
)
from risk_model import load_rules, load_pest_config, load_weather, run_model
from chart import build_report


def _hemisphere_note() -> str:
    return (
        "Southern Hemisphere (GDD reset July)"
        if GDD_RESET_MONTH == 7
        else "Northern Hemisphere (GDD reset January)"
    )


def select_crops_interactive(available: list[str]) -> list[str]:
    """Interactive crop selection for terminal use."""
    print("\nAvailable crops:\n")
    for i, crop in enumerate(available, 1):
        print(f"  {i:>2}.  {crop}")
    print(f"\n  A.   All crops")
    print()
    while True:
        raw = input("Select numbers separated by commas (e.g. 1,3,5) or A for all: ").strip()
        if raw.upper() == 'A':
            return available
        try:
            indices = [int(x.strip()) for x in raw.split(',')]
            selected = [available[i - 1] for i in indices if 1 <= i <= len(available)]
            if selected:
                return selected
        except (ValueError, IndexError):
            pass
        print("  Invalid input — try again.\n")


def print_validation_summary(df: pd.DataFrame):
    """Print per-pest risk distribution statistics."""
    print("\n" + "=" * 68)
    print("  VALIDATION SUMMARY")
    print("=" * 68)
    print(f"  {'Crop':<14} {'Pest (short)':<30} {'Crit':>5} {'High':>5} {'Mod':>5} {'Low':>5}")
    print("  " + "-" * 64)
    for (crop, pest), grp in df.groupby(['crop', 'pest']):
        inseas = grp[grp['risk_class'] != 'Out of season']
        if inseas.empty:
            continue
        n_c = (inseas['risk_class'] == 'Critical').sum()
        n_h = (inseas['risk_class'] == 'High').sum()
        n_m = (inseas['risk_class'] == 'Moderate').sum()
        n_l = (inseas['risk_class'] == 'Low').sum()
        pname = pest.split(',')[-1].strip()[:28]
        print(f"  {crop:<14} {pname:<30} {n_c:>5} {n_h:>5} {n_m:>5} {n_l:>5}")
    print("=" * 68)
    total = len(df)
    oos   = (df['risk_class'] == 'Out of season').sum()
    n_c   = (df['risk_class'] == 'Critical').sum()
    n_h   = (df['risk_class'] == 'High').sum()
    print(f"\n  Total records: {total:,}  |  Out of season: {oos:,}")
    print(f"  Critical: {n_c:,}  |  High: {n_h:,}")
    print(f"  Hemisphere: {_hemisphere_note()}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Agrobit iAgro — Pest & Disease Fuzzy Risk Model v2.0'
    )
    parser.add_argument('--crop',      nargs='+', metavar='CROP',
                        help='One or more crop names to analyse')
    parser.add_argument('--all',       action='store_true',
                        help='Analyse all available crops')
    parser.add_argument('--no-report', action='store_true',
                        help='Skip PDF report generation')
    parser.add_argument('--validate',  action='store_true',
                        help='Print validation statistics table')
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  Agrobit iAgro — Fuzzy Pest Risk Model  v2.0")
    print(f"  {_hemisphere_note()}")
    print("=" * 60)

    # ── Load data ─────────────────────────────────────────────────────────
    print("\nLoading data...")
    try:
        rules       = load_rules(RULES_EXCEL)
        pest_config = load_pest_config(PEST_CONFIG_JSON)
        weather_df  = load_weather(WEATHER_EXCEL)
    except FileNotFoundError as e:
        print(f"\n  ERROR: {e}")
        print("  Make sure db_crops_PDM.xlsx, pest.json and weather_data.xlsx")
        print("  are in the same directory as main.py")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ERROR loading data: {e}")
        sys.exit(1)

    period = (f"{weather_df['date'].min().date()} → "
              f"{weather_df['date'].max().date()} "
              f"({len(weather_df)} days)")
    print(f"  Weather: {period}")
    print(f"  Rules loaded: {len(rules)} "
          f"(Critical: {sum(1 for r in rules if r['risk']=='Critical')}, "
          f"High: {sum(1 for r in rules if r['risk']=='High')})")
    print(f"  Pest configs: {len(pest_config)} entries")

    # ── Crop selection ────────────────────────────────────────────────────
    available = sorted(set(r['crop'] for r in rules))

    if args.all:
        selected = available
    elif args.crop:
        # Fuzzy match: case-insensitive
        selected = []
        for c in args.crop:
            match = next((a for a in available if a.lower() == c.lower()), None)
            if match:
                selected.append(match)
            else:
                close = [a for a in available if c.lower() in a.lower()]
                if close:
                    print(f"  '{c}' not found. Did you mean: {', '.join(close)}?")
                else:
                    print(f"  '{c}' not found. Available: {', '.join(available)}")
                sys.exit(1)
    else:
        selected = select_crops_interactive(available)

    print(f"\nSelected: {', '.join(selected)}\n")

    # ── Run model ─────────────────────────────────────────────────────────
    print("Running fuzzy risk model...")
    t0 = time.time()
    all_results = []
    for i, crop in enumerate(selected, 1):
        print(f"  [{i}/{len(selected)}] {crop}...", end='', flush=True)
        try:
            df = run_model(
                rules       = rules,
                pest_config = pest_config,
                weather_df  = weather_df.copy(),
                filter_crop = crop,
            )
            all_results.append(df)
            n_crit = (df['risk_class'] == 'Critical').sum()
            n_high = (df['risk_class'] == 'High').sum()
            print(f" done  (Critical: {n_crit}, High: {n_high})")
        except Exception as e:
            print(f" ERROR: {e}")

    if not all_results:
        print("No results produced. Exiting.")
        sys.exit(1)

    results_df = pd.concat(all_results, ignore_index=True)
    elapsed = time.time() - t0
    print(f"\nModel completed in {elapsed:.1f}s — {len(results_df):,} records")

    # ── Save CSV ──────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / 'risk_results.csv'
    results_df.to_csv(csv_path, index=False)
    print(f"Results CSV: {csv_path}")

    # ── Validation summary ────────────────────────────────────────────────
    if args.validate or True:   # always show summary
        print_validation_summary(results_df)

    # ── PDF report ────────────────────────────────────────────────────────
    if not args.no_report:
        print("Generating PDF report...")
        try:
            build_report(results_df, weather_df, OUTPUT_DIR / REPORT_FILENAME)
        except Exception as e:
            print(f"  Report generation error: {e}")
            print("  CSV results are still available.")


if __name__ == '__main__':
    main()
