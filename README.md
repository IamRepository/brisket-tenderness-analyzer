# Brisket Tenderness Analyzer

A Streamlit app that estimates how far a brisket has rendered (become tender), from the temperature log of a cook and hold. Upload the log files, confirm what each column measures, and the app reports a tenderness assessment for the Point, the Flat and the whole brisket, with charts and a downloadable PDF report.

Independent implementation based on brisket time-temperature and hot-hold concepts shared by Steve Gow. Results are analytical estimates.

## Using it

1. **Upload** a primary temperature file and, optionally, a secondary one (CSV, XLSX, XLSM or XLS). A typical split is the smoker log as primary and the hold-oven log as secondary. The app reads:
   - comma or dot decimals, and comma, semicolon or tab separators, in UTF-8 or Windows encoding;
   - ISO dates (2026-06-01), day-first dates (01.06.2026, 01/06/2026) and, when the file shows it, month-first dates;
   - times with a time zone (+02:00, Z, CEST, CET, MESZ, MEZ, UTC), which are shown in your local time;
   - times without a zone, read as local time in your browser's time zone; the end-of-summer-time clock change is handled;
   - separate date and time columns, spreadsheet date-times and spreadsheet date numbers.

   A note under each upload says how its times were read.
2. **Assign roles** to each temperature column. The app suggests one from the column name:
   - **Brisket - Point** and **Brisket - Flat**: meat probes. Several probes at the same location are averaged over time.
   - **Cook Environment - PID**: the cooker's own reading (controller or cavity temperature).
   - **Cook Environment - Grate**: a probe at the grate.
   - **Hold Environment - Probe**: the hold oven or cabinet.
   - **Ignore**: any column to leave out.
3. **Analyse session.** Results stay on screen; changing a role or a setting updates them. The PDF download button appears at the top of the page.
4. **Check the smoker-to-hold transfer.** It is estimated from where the brisket temperatures peak. If you know when the brisket left the smoker, set it under "Smoker-to-hold transfer".

A **Dark mode** switch sits beside the title.

## What it reports

- **Whole brisket tenderness assessment.** Overall rendering is the mean of the Point and Flat totals. A note appears when the two ends differ widely.
- **Session detection summary.** Session type, cook and hold hours, temperatures and sampling interval for each location.
- **Environment.** Which cook and hold sensors are present, their temperatures, and stability. Stability ignores planned setpoint steps.
- **Rendering by location.** Accumulated rendering over time, with the ideal range (95-105 %) shaded. Below it, a band breakdown shows which temperature bands the rendering came from, split into cook and hold.
- **PDF report.** It opens with a one-page summary, followed by session details, environment, a section per location with the band calculation, and the method and assessment scale.

## How rendering is calculated

- Time at or above 60 °C adds rendering at an hourly rate that rises with temperature, across ten bands.
- Time below 60 °C, and recording gaps longer than the gap limit, add nothing.
- The cook and the hold split at the smoker-to-hold transfer. It is estimated from where the brisket probes peak, or set by hand; every probe is split at the same time.
- The assessment scale:

| Recorded total | Assessment |
|---|---|
| Below 80 % | Underdone and tight |
| 80 % to below 95 % | Slightly tight but sliceable |
| 95 % to 105 % | Ideal tenderness |
| Above 105 % to 120 % | Very soft and potentially overdone |
| Above 120 % | Increased risk of mushy or over-rendered texture |

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app: page layout, steps, tables and charts |
| `brisket_engine.py` | Reading files, session detection, combining probes, rendering calculation |
| `pit_engine.py` | Column role suggestions and environment statistics (stability, level changes) |
| `pdf_report.py` | The PDF report |
| `test_analyzer.py` | Tests for the engines |
| `requirements.txt` | Exact library versions the app is tested with |
| `brisket-analyzer-notes.md` | Developer notes: conventions, design decisions, open issues |

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
python -m unittest test_analyzer
```

## Deploying on Streamlit Community Cloud

After uploading a release that changes more than `app.py`, reboot the app (**Manage app → ⋮ → Reboot app**). Otherwise Streamlit can keep the previous version of the helper modules in memory.
