# Brisket Tenderness Analyzer: code notes

Last updated 6 October 2026 for v2.8.2. Repo: github.com/IamRepository/brisket-tenderness-analyzer (public). Streamlit app on Streamlit Community Cloud; files are uploaded to GitHub manually.

## Name and release conventions
- Product name: Brisket Tenderness Analyzer (US spelling). It replaced "Brisket Session Analyser" in v2.8.1.
- Where the name appears: app.py (APP_NAME, used for page_title and st.title), pdf_report.py (report title, page footer), README.md title, release notes titles, the zip and its inner folder.
- Release zip: "Brisket Tenderness Analyzer vX.Y.Z.zip", inner folder named the same, containing only the changed files.
- Every release zip includes this file, updated for that release, at the repo root.
- After uploading a release that changes more than app.py, reboot the app (Manage app, ⋮, Reboot app). Otherwise Streamlit can keep the previous brisket_engine.py in memory; v2.8.0 failed with AttributeError on combine_probes until a reboot.

## Files
- app.py: whole UI and orchestration. Title, PDF button slot, Step 1 uploads (two columns), Step 2 role pickers (two columns), "Analyse session" -> consolidate Point/Flat -> derive transfer -> environment segmentation -> session summary (HTML table), advanced sampling settings, environment integrity (HTML table, centred), whole-brisket cards, tables, Plotly charts, PDF download button at the top.
- brisket_engine.py: parsing (parse_ts, parse_temp, read_file, detect_columns, prepare), classify_session (Cook + Hold / Hold Only / Calibration / Cook Only / Uncertain, pull = smoothed peak), analyse (10 temperature bands from 60 C, ZONE_RATES per hour, cook/hold split at pull, gaps above max_gap excluded), assess (thresholds 80/95/105/120 %), combine_probes.
- pit_engine.py: role constants (Point, Flat, Cook PID, Cook Grate, Hold Env, Ignore), classify_columns by name hints then shape, prepare, analyse (avg/min/max, steady_stability score and level_changes, lid-drop events for grate), align (unused).
- master_timeline.py: MasterTimeline helpers. Not imported by app.py; the app reimplements the master start and transfer marker.
- pdf_report.py: ReportLab A4 portrait report. Order: summary (results, spread callout, session times, main chart), session details, environment (composite only with several hold sensors), one section per location, method + assessment scale + configuration. Table text is Paragraphs, so colour/alignment/bold must come from paragraph styles (HeadCell, CellC) or <b> markup, not TableStyle.
- tests/: test_master_timeline, test_v28, test_v282 (pass); test_v24 (test_classification fails: expects pre-2.7 role names).

## Model
Rendering = sum over intervals of hours x band rate, using the temperature at the start of each interval. Whole-brisket = mean of canonical Point and Flat totals. Transfer = median of Cook + Hold pull times; cook/hold hours (phase_stats) and rendering are both split at it.
Stability = 100 - 4 x std(deviation from 60-min rolling median) - 8 x median reading-to-reading change, over steady readings only (baseline moved <= 2.5 C in 15 min). Level changes = held levels (>= 30 min) differing by >= 5 C.
Spread note when Point and Flat assessments differ or totals differ by >= 25 points (SPREAD_WARNING in app.py).

## UI state (v2.8.1)
- st.session_state.analysed keeps results visible after "Analyse session"; it resets when the uploaded files change (file_id pair in analysed_files).
- Sampling settings use widget keys override_gap and manual_gap_seconds. They are read from session_state at the top of the run, because the widgets are drawn after the summary table.
- html_table() and stat_cards() render wrapping HTML tables and cards. Styles are in TABLE_CSS (bta-* classes, theme-neutral rgba colours).

## History
- v2.8.0: comma decimals in parse_temp; combine_probes averages same-location probes on a shared time grid (each probe held up to its own gap threshold); reference brisket removed; "Pointt" display fix.
- v2.8.1: renamed to Brisket Tenderness Analyzer; layout changes (PDF button on top, side-by-side uploads and roles, settings under the summary, fitted summary table, centred integrity table, wrapping assessment cards); results persist across reruns.
- v2.8.2: cook/hold hours split at the transfer; stepped-program stability and level changes; Point/Flat spread note; PDF restructured (summary first, 9 -> 5 pages, method and assessment scale, file names, °C, readable headers, local generated time).

## Open issues
1. test_v24 classification test is out of date.
2. __pycache__ committed, no .gitignore; requirements unpinned (tested on pandas 3.0.5 / Streamlit 1.65); use_container_width is deprecated.
3. master_timeline.py and pit.align are dead code; lid events computed but never shown.
4. PDF charts: every reading drawn as a dot, y-axis title overlaps tick labels, serif tick font, legend below the chart, "Transfer" label collides with the curve, no gridlines. Planned as the next release.
5. Band rates give high totals for long holds near 90 C (the synthetic 16 h test cook scored 168 %). Worth checking against real cooks.
6. Other tables (configuration, probe comparison, environment analysis) still use st.dataframe and may scroll sideways on narrow screens.
