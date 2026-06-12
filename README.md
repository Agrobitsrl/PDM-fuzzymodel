# iAgro Pest & Disease Fuzzy Risk Model (PDM)

Agrobit S.r.l. — SmartCherry sub-innovation project (SIP10, code RZTHQ),
OpenAgri Open Call, Horizon Europe Grant Agreement No. 101134083.

Open-source, multi-crop fuzzy-logic engine that estimates **daily pest and
disease infection risk** from weather data and crop phenology.

## What it does

For every day of the season and every configured crop–pest pair, the model
computes a 0–100 infection-risk score and a risk class (Low, Moderate, High,
Critical), turning weather and agronomic knowledge into actionable, site-specific
guidance on when monitoring or treatment is warranted. It supports 18 fruit, nut
and vine crops, with cherry as the primary SmartCherry use case.

Pipeline: weather + crop–pest rule base + pest biological parameters → feature
engineering (GDD accumulation, leaf-wetness estimation, moving averages, streaks)
→ fuzzy Mamdani inference and defuzzification → daily risk CSV + PDF report.

## Install

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python main.py                 # interactive crop selection
python main.py --crop Cherry   # single crop
python main.py --crop Cherry Grape   # multiple crops
python main.py --all           # all crops
python main.py --no-report     # skip PDF generation
python main.py --validate      # print validation statistics
```

Outputs are written to `output/`: `risk_results.csv` (20-column daily risk table)
and `pest_risk_report.pdf` (human-readable report).

## Files

- `main.py` — CLI entry point (crop selection, run, validation summary, output)
- `config.py` — all user-editable settings (paths, fuzzy parameters, risk
  thresholds, GDD/phenology, leaf-wetness tables, report/colour settings)
- `risk_model.py` — core engine: data loading, feature computation, membership
  functions, per-day Mamdani inference (`run_model`)
- `chart.py` — `build_report()`, the multi-section PDF risk report
- `test_model_v4.py` — tests (parsing, membership functions, end-to-end scoring)
- `db_crops_PDM.xlsx` — crop–pest fuzzy rule base (crop, pest, humidity, temp,
  rainfall, risk, type)
- `pest.json` — per-crop, per-pest biological/phenological parameters
- `weather_data.xlsx` — daily weather input (date, temp_max, temp_min, humidity,
  rainfall)
- `docs/` — model documentation
- `output/` — generated results (git-ignored)

## Input / Output

- **Input:** weather XLSX (date, temp_max, temp_min, humidity, rainfall, auto
  date format), the rule base XLSX and the pest JSON.
- **Output:** `risk_results.csv` (daily per-crop, per-pest scores and classes)
  and `pest_risk_report.pdf`.

All tuning parameters live in `config.py`.

## Requirements

- Python 3.10+
- pandas, numpy, openpyxl, xlrd, matplotlib, reportlab

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Copyright 2026 Agrobit S.r.l.

Developed within the SmartCherry sub-innovation project (SIP10, code RZTHQ)
funded by the OpenAgri Open Call under the European Union's Horizon Europe
research and innovation programme, Grant Agreement No. 101134083.
