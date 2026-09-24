# Brisket Tenderness Streamlit App

A first working browser app for analysing brisket probe exports against the Celsius tenderness model used in the earlier calculator.

## Features

- Upload `.xlsx`, `.xlsm`, `.xls`, or `.csv` files
- Detect timestamp and one or more temperature columns
- Parse timestamps such as `23/05/2026 10:49:35 CEST`
- Handle data that crosses midnight
- Remove invalid rows and duplicate timestamps
- Exclude configurable recording gaps
- Analyse Cook only, Hold/Cooldown only, or split at a pull timestamp
- Show Cook, Hold and total estimated rendering
- Display a temperature chart and band-by-band calculation
- Export an Excel analysis workbook

## Run locally

1. Install Python 3.10 or newer.
2. Open a terminal in this folder.
3. Create an optional virtual environment:

```bash
python -m venv .venv
```

4. Activate it.

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

5. Install dependencies:

```bash
pip install -r requirements.txt
```

6. Start the app:

```bash
streamlit run app.py
```

The app opens in the default browser. A phone on the same network can use the Network URL printed by Streamlit, subject to computer firewall and network settings.

## Input format

The app expects:

- one timestamp column
- one or more Celsius temperature columns

Example:

| timestamp | Flat | Point |
|---|---:|---:|
| 23/05/2026 10:49:35 CEST | 95.4 | 96.1 |
| 23/05/2026 10:49:36 CEST | 95.4 | 96.1 |

The app can also analyse a single temperature column.

## Calculation notes

- Elapsed duration is calculated from each timestamp to the next timestamp.
- Each interval uses the earlier reading's temperature.
- Intervals above the selected maximum gap are excluded.
- Temperatures below 60°C are reported but do not contribute to the model.
- In split mode, an interval crossing the pull timestamp is divided between Cook and Hold.
- The app uses zone-weighted rates from the existing Celsius calculator.

## Files

- `app.py`: Streamlit interface
- `brisket_engine.py`: parsing, validation, analysis and Excel export
- `tests/test_engine.py`: executable tests

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Disclaimer

This calculator provides an estimate based on time and internal temperature. Actual tenderness varies. Use probe tenderness and appropriate food-safety practices alongside the model.
