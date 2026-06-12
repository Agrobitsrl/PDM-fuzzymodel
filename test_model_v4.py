import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, '/sessions/admiring-festive-shannon/mnt/py_pdm_update')
from risk_model import (
    _parse_temp, _parse_humidity, _parse_rainfall,
    membership_temp, membership_humidity, membership_rainfall, membership_phenology,
    load_rules, load_pest_config, load_weather, compute_features, score_day, run_model
)
from config import RISK_THRESHOLD_CRITICAL, RISK_THRESHOLD_HIGH, RISK_THRESHOLD_MODERATE

passed = failed = 0

def check(name, got, expected, tol=1e-6):
    global passed, failed
    if isinstance(expected, tuple):
        try:    ok = all(abs(g - e) < tol for g, e in zip(got, expected))
        except: ok = (got == expected)
    else:
        try:    ok = abs(float(got) - float(expected)) < tol
        except: ok = False
    if ok:  passed += 1; print(f'  [PASS] {name}')
    else:   failed += 1; print(f'  [FAIL] {name}: got={got!r} expected={expected!r}')

def check_ge(name, got, threshold):
    global passed, failed
    if float(got) >= float(threshold):
        passed += 1; print(f'  [PASS] {name} ({got} >= {threshold})')
    else:
        failed += 1; print(f'  [FAIL] {name}: got={got!r} expected >= {threshold}')

def check_lt(name, got, threshold):
    global passed, failed
    if float(got) < float(threshold):
        passed += 1; print(f'  [PASS] {name} ({got} < {threshold})')
    else:
        failed += 1; print(f'  [FAIL] {name}: got={got!r} expected < {threshold}')

BASE = Path('/sessions/admiring-festive-shannon/mnt/py_pdm_update')
rules  = load_rules(BASE / 'db_crops_PDM.xlsx')
pests  = load_pest_config(BASE / 'pest.json')
wx     = load_weather(BASE / 'weather_data.xlsx')
wx_f   = compute_features(wx)

def make_row(**kwargs):
    row = wx_f.iloc[150].copy()
    for k, v in kwargs.items():
        row[k] = v
    return row

# ══════════════════════════════════════════════════════════════════════════════
print('=== 1. Vineyard crop label ===')
vin_rules  = [r for r in rules if r['crop'] == 'Vineyard']
grape_rules = [r for r in rules if r['crop'] == 'Grape']
check('Vineyard rules loaded', float(len(vin_rules) > 0), 1.0)
check('No legacy Grape rules', float(len(grape_rules) == 0), 1.0)
print(f'  Vineyard rules: {len(vin_rules)} (legacy Grape rules remaining: {len(grape_rules)})')

# ══════════════════════════════════════════════════════════════════════════════
print()
print('=== 2. pheno_t_base — Erwinia amylovora ===')
erp = pests.get(('Pear','Erwinia amylovora'))
check('Erwinia pheno_t_base=5', erp.get('pheno_t_base'), 5)
check('Erwinia t_base=18 unchanged', erp['t_base'], 18.0)
check('Erwinia pheno_frac_lo=0.09', erp['pheno_frac_lo'], 0.09)
check('Erwinia pheno_frac_hi=0.27', erp['pheno_frac_hi'], 0.27)
# Verify: in January (GDD_5b low), Erwinia should still be OOS
erw_rules = [r for r in rules if r['crop']=='Pear' and 'Erwinia' in r['pest']]
jan_row = wx_f[wx_f['date'].dt.month == 1].iloc[5].copy()
res_jan_erw = score_day(jan_row, erw_rules, 'Erwinia amylovora', erp)
print(f'  Erwinia Jan GDD_5b={jan_row["gdd_cum_5b"]:.0f} → {res_jan_erw["risk_class"]} (score={res_jan_erw["score"]:.1f})')
# In April (GDD_5b typically 200-600), Erwinia should be in season
apr_row = wx_f[(wx_f['date'].dt.month == 4) & (wx_f['gdd_cum_5b'] > 150)].iloc[0].copy()
apr_row['temp_avg'] = 22.0; apr_row['temp_avg_7d'] = 22.0
apr_row['humidity'] = 92.0; apr_row['humidity_7d'] = 92.0
apr_row['rain_3d'] = 8.0; apr_row['rainfall'] = 8.0
apr_row['streak_hum70'] = 3
res_apr_erw = score_day(apr_row, erw_rules, 'Erwinia amylovora', erp)
print(f'  Erwinia Apr T=22 RH=92 → {res_apr_erw["risk_class"]} (score={res_apr_erw["score"]:.1f})')
check_ge('Erwinia in April can score above Low', res_apr_erw['score'], 10.0)

# ══════════════════════════════════════════════════════════════════════════════
print()
print('=== 3. Taphrina deformans — corrected phenological window ===')
tp_fruits = pests.get(('Peach','Taphrina deformans (fruits)'))
tp_shoots = pests.get(('Peach','Taphrina deformans (shoots)'))
check('Taphrina fruits pheno_frac_hi=0.159', tp_fruits['pheno_frac_hi'], 0.159)
check('Taphrina fruits t_lethal_max=19.0', tp_fruits['t_lethal_max'], 19.0)
check('Taphrina shoots t_lethal_max=19.0', tp_shoots['t_lethal_max'], 19.0)
tph_rules = [r for r in rules if r['crop']=='Peach' and 'Taphrina' in r['pest'] and 'fruits' in r['pest']]
# July: GDD_5b > 1000 → must be Out of season with tightened window
jul_row = wx_f[(wx_f['date'].dt.month == 7)].iloc[0].copy()
print(f'  July GDD_5b={jul_row["gdd_cum_5b"]:.0f}')
res_jul = score_day(jul_row, tph_rules, 'Taphrina deformans (fruits)', tp_fruits)
print(f'  Taphrina July → {res_jul["risk_class"]} (score={res_jul["score"]:.1f})')
check('Taphrina (fruits) July = Out of season', float(res_jul['risk_class'] == 'Out of season'), 1.0)
# Also: T=22°C → above t_lethal_max=19 → score=0
res_hot = score_day(
    make_row(temp_avg=22.0, temp_avg_7d=22.0, gdd_cum_5b=200.0, gdd_annual_ref_5b=2200.0),
    tph_rules, 'Taphrina deformans (fruits)', tp_fruits)
print(f'  Taphrina T=22°C → {res_hot["risk_class"]} (score={res_hot["score"]:.1f}) detail={res_hot["detail"]}')
check('Taphrina T=22°C → suppressed (t_lethal_max)', res_hot['score'], 0.0)

# ══════════════════════════════════════════════════════════════════════════════
print()
print('=== 4. Oidium mangiferae — Mediterranean phenological window ===')
oid_p = pests.get(('Mango','Oidium mangiferae'))
check('Oidium pheno_frac_lo=0.08', oid_p['pheno_frac_lo'], 0.08)
check('Oidium pheno_frac_hi=0.33', oid_p['pheno_frac_hi'], 0.33)
check('Oidium pheno_t_base=10', oid_p.get('pheno_t_base', oid_p['t_base']), 10)
# August: GDD_10b high, should be OOS for Oidium
oid_rules = [r for r in rules if r['crop']=='Mango' and 'Oidium' in r['pest']]
aug_row = wx_f[(wx_f['date'].dt.month == 8)].iloc[0].copy()
print(f'  August GDD_10b={aug_row["gdd_cum_10b"]:.0f}')
res_aug_oid = score_day(aug_row, oid_rules, 'Oidium mangiferae', oid_p)
print(f'  Oidium August → {res_aug_oid["risk_class"]} (score={res_aug_oid["score"]:.1f})')
check('Oidium August Out of season', float(res_aug_oid['risk_class'] == 'Out of season'), 1.0)

# ══════════════════════════════════════════════════════════════════════════════
print()
print('=== 5. Myzus persicae — heat inhibition ===')
myz_p = pests.get(('Peach','Myzus persicae'))
check('Myzus t_lethal_max=30', myz_p['t_lethal_max'], 30.0)
myz_rules = [r for r in rules if r['crop']=='Peach' and 'Myzus' in r['pest']]
# T=32 > t_lethal_max=30 → score=0
res_hot_myz = score_day(
    make_row(temp_avg=32.0, humidity=70.0, rain_3d=1.0, rainfall=1.0,
             gdd_cum_5b=800.0, gdd_annual_ref_5b=2200.0),
    myz_rules, 'Myzus persicae', myz_p)
print(f'  Myzus T=32°C → {res_hot_myz["risk_class"]} (score={res_hot_myz["score"]:.1f}) detail={res_hot_myz["detail"]}')
check('Myzus T=32°C → score=0', res_hot_myz['score'], 0.0)

# ══════════════════════════════════════════════════════════════════════════════
print()
print('=== 6. Plasmopora viticola — daily rain window + correct T range ===')
pla_p = pests.get(('Vineyard','Plasmopora viticola'))
check('Plasmopora rain_window_days=1', pla_p.get('rain_window_days'), 1)
pla_rules = [r for r in rules if r['crop']=='Vineyard' and 'Plasmopora' in r['pest_key']]
print(f'  Plasmopora rules count: {len(pla_rules)}')
# Favourable spring day: T=20, RH=75, daily rain=12mm (> 10mm threshold)
res_spring_pla = score_day(
    make_row(temp_avg=20.0, temp_avg_7d=20.0, humidity=75.0, humidity_7d=75.0,
             rainfall=12.0, rain_3d=8.0, streak_hum70=4,
             gdd_cum_8b=300.0, gdd_annual_ref_8b=1800.0),
    pla_rules, 'Plasmopora viticola', pla_p)
print(f'  Plasmopora spring T=20 RH=75 rain_daily=12mm → {res_spring_pla["risk_class"]} score={res_spring_pla["score"]:.1f}')
check_ge('Plasmopora with adequate daily rain → High or better', res_spring_pla['score'], RISK_THRESHOLD_HIGH)

# ══════════════════════════════════════════════════════════════════════════════
print()
print('=== 7. Botrytis cinerea — differentiated High vs Critical ===')
bot_p = pests.get(('Vineyard','Botrytis cinerea'))
bot_rules = [r for r in rules if r['crop']=='Vineyard' and 'Botrytis' in r['pest']]
# T=20, RH=82 → High rule (75-90%) fires, Critical (>90%) doesn't → score < Critical
res_bot_high = score_day(
    make_row(temp_avg=20.0, temp_avg_7d=20.0, humidity=82.0, humidity_7d=82.0,
             rainfall=2.0, rain_3d=5.0, streak_hum70=5,
             gdd_cum_0b=800.0, gdd_annual_ref_0b=2000.0),
    bot_rules, 'Botrytis cinerea', bot_p)
print(f'  Botrytis T=20 RH=82 → {res_bot_high["risk_class"]} score={res_bot_high["score"]:.1f}')
check_ge('Botrytis RH=82 → at least High', res_bot_high['score'], RISK_THRESHOLD_HIGH)
check_lt('Botrytis RH=82 → below Critical', res_bot_high['score'], RISK_THRESHOLD_CRITICAL)

# T=20, RH=95 → Critical
# wetness_h must also be set: _wetness_hours(RH=95, rain=2mm) = 24h, which
# satisfies Botrytis min_wetness_hours_critical=15. make_row inherits the
# base row's wetness_h=12h (computed from original RH=83), so override it.
res_bot_crit = score_day(
    make_row(temp_avg=20.0, temp_avg_7d=20.0, humidity=95.0, humidity_7d=95.0,
             rainfall=2.0, rain_3d=5.0, streak_hum70=5,
             wetness_h=24.0, wetness_3d=60.0,
             gdd_cum_0b=800.0, gdd_annual_ref_0b=2000.0),
    bot_rules, 'Botrytis cinerea', bot_p)
print(f'  Botrytis T=20 RH=95 → {res_bot_crit["risk_class"]} score={res_bot_crit["score"]:.1f}')
check_ge('Botrytis RH=95 → Critical', res_bot_crit['score'], RISK_THRESHOLD_CRITICAL)

# ══════════════════════════════════════════════════════════════════════════════
print()
print('=== 8. End-to-end run ===')
results = run_model(rules, pests, wx)
check('No legacy Grape in crop column', float('Grape' not in results.crop.unique()), 1.0)
check('Vineyard present in crop column', float('Vineyard' in results.crop.unique()), 1.0)
check('Total records', len(results), 40205)
check('No NaN risk_score', float(results['risk_score'].isna().sum()), 0)
check('VPD column present', float('vpd' in results.columns), 1.0)

print()
print('  Risk distribution:')
for cls, cnt in results['risk_class'].value_counts().items():
    print(f'    {cls}: {cnt}')

# Vineyard-specific validation
grape_res = results[results['crop'] == 'Vineyard']
print(f'\n  Vineyard records: {len(grape_res)}')
print('  Vineyard risk distribution:')
for cls, cnt in grape_res['risk_class'].value_counts().items():
    print(f'    {cls}: {cnt}')

# Taphrina (fruits) summer check
peach_taf = results[(results['crop']=='Peach') & (results['pest'].str.contains('Taphrina.*fruits', regex=True))]
summer_taf = peach_taf[peach_taf['date'].dt.month.isin([6,7,8,9])]
print(f'\n  Taphrina (fruits) summer records: {len(summer_taf)}')
summer_high = summer_taf[summer_taf['risk_class'].isin(['High','Critical'])]
print(f'  Taphrina (fruits) summer High/Critical: {len(summer_high)} (should be ~0)')
check('Taphrina no summer High/Critical', float(len(summer_high) == 0), 1.0)

# Erwinia: no July Critical
pear_erw = results[(results['crop']=='Pear') & results['pest'].str.contains('Erwinia')]
jul_erw = pear_erw[(pear_erw['date'].dt.month == 7) & (pear_erw['risk_class'] == 'Critical')]
print(f'  Erwinia July Critical: {len(jul_erw)} (should be 0)')
check('Erwinia no July Critical', float(len(jul_erw) == 0), 1.0)

print()
print(f'TOTAL: {passed} passed, {failed} failed')
if failed == 0:
    print('[ALL TESTS PASSED]')
else:
    print(f'[{failed} TESTS FAILED]')
