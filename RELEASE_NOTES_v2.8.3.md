# Brisket Tenderness Analyzer v2.8.3

## App

- **New: Rendering by location**, replacing "Brisket probe analysis". That section repeated the summary table and the comparison chart.
  - **Accumulated rendering over time** for Point and Flat, with the ideal range (95–105 %) shaded and the transfer marked.
  - Below the chart, one line per location says when it reached 95 % and when it passed 105 %, each with "during the cook" or "during the hold", and its final total.
  - **Band breakdown** per location: which temperature bands the rendering came from, stacked into cook and hold. Hover shows hours and rate per band.
- Charts use one fixed colour per entity everywhere (Point blue, Flat orange, PID green, Grate yellow, Hold pink). The palette is checked for colour-blind separation.
- Chart lines now break at recording gaps instead of drawing straight across them.
- Chart styling: legend above the plot, light gridlines, and a neutral grey transfer marker labelled above the plot.
- The configuration, probe comparison and environment tables now use the page-width tables that wrap their text, instead of scrolling sideways.

## PDF report

- Charts redrawn:
  - lines only, with no dot on every reading;
  - lines break at recording gaps;
  - one fixed colour per entity, matching the app;
  - the legend sits above the plot;
  - light gridlines and round axis steps;
  - Helvetica throughout;
  - a rotated axis title that no longer overlaps the numbers;
  - the transfer label sits above the plot, clear of the curves.
- Added the accumulated rendering chart, with the ideal band, after "Rendering by profile".
- Location sections no longer repeat that location's temperature chart from page 1. They keep the metrics and the band calculation.
- Headings stay with their tables instead of being stranded at the bottom of a page.
- Dropped the timeline table under Configuration; the summary already shows the same session start and transfer.

## Cleanup

- `requirements.txt` pins the exact versions tested: Streamlit 1.65.0, pandas 3.0.5, numpy 2.5.3, plotly 7.1.0, reportlab 5.0.1, openpyxl 3.1.5, xlrd 2.0.2.
- Added `.gitignore`, so `__pycache__` and similar clutter are never committed.
- Replaced the deprecated `use_container_width` with `width="stretch"`.
- Removed unused code: `pit_engine.align` and an unused import in `brisket_engine.py`.
- Rewrote `README.md`, which still described v2.4.
- Tests are now in one top-level file, `test_analyzer.py`, so they survive upload. It has 14 tests, including an up-to-date column-role test.
