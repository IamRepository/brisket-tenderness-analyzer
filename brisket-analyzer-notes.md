# Brisket Tenderness Analyzer: code notes

Last updated 6 October 2026 for v2.8.3. Repo: github.com/IamRepository/brisket-tenderness-analyzer (public). Streamlit app on Streamlit Community Cloud; files are uploaded to GitHub manually through the web interface.

## Name and release conventions
- Product name: Brisket Tenderness Analyzer (US spelling). It replaced "Brisket Session Analyser" in v2.8.1.
- Where the name appears: app.py (APP_NAME, used for page_title and st.title), pdf_report.py (report title, page footer), README.md title, release notes titles, the zip and its inner folder.
- Release zip: "Brisket Tenderness Analyzer vX.Y.Z.zip", inner folder named the same, containing only the changed files.
- Every release zip includes this file, updated for that release, at the repo root.
- Keep files at the top level. Subfolders (such as tests/) did not survive the manual upload twice.
- After uploading a release that changes more than app.py, reboot the app (Manage app, ⋮, Reboot app). Otherwise Streamlit can keep the previous brisket_engine.py in memory; v2.8.0 failed with AttributeError on combine_probes until a reboot.
- Only the newest RELEASE_NOTES file is kept in the repo; this file's History section is the long-term record.

## Files
- app.py: whole UI and orchestration. Title, PDF button slot, Step 1 uploads (two columns), Step 2 role pickers (two columns), "Analyse session" -> consolidate Point/Flat -> derive transfer -> environment segmentation -> session summary, advanced sampling settings, environment integrity, whole-brisket cards and spread note, probe comparison (table + chart), environment analysis (table + chart), Rendering by location (accumulated chart + milestone lines + band breakdown per location). All tables are html_table().
- brisket_engine.py: parsing (parse_ts, parse_temp, read_file, detect_columns, prepare), classify_session (Cook + Hold / Hold Only / Calibration / Cook Only / Uncertain, pull = smoothed peak), analyse (10 temperature bands from 60 C, ZONE_RATES per hour, cook/hold split at pull, gaps above max_gap excluded; Result.timeline holds per-interval and accumulated rendering), assess (thresholds 80/95/105/120 %), combine_probes.
- pit_engine.py: role constants (Point, Flat, Cook PID, Cook Grate, Hold Env, Ignore), classify_columns by name hints then shape, prepare, analyse (avg/min/max, steady_stability score and level_changes, lid-drop events for grate).
- pdf_report.py: ReportLab A4 portrait report. Order: summary (results, spread callout, session times, main chart), session details (timing, temperatures, rendering by profile, accumulated rendering chart), environment (composite only with several hold sensors), one section per location (metrics + band table), method + assessment scale, configuration. Table text is Paragraphs, so colour/alignment/bold must come from paragraph styles (HeadCell, CellC) or <b> markup, not TableStyle. _chart() draws its own lines, grid, legend and labels (no LinePlot).
- test_analyzer.py: 14 tests on the engines (parsing, roles, combine_probes, session, assessment scale, rendering rate, stability). Run: python -m unittest test_analyzer.
- requirements.txt: exact versions tested. .gitignore: keeps __pycache__ out.

## Model
Rendering = sum over intervals of hours x band rate, using the temperature at the start of each interval. Whole-brisket = mean of canonical Point and Flat totals. Transfer = median of Cook + Hold pull times; cook/hold hours (phase_stats) and rendering are both split at it.
Stability = 100 - 4 x std(deviation from 60-min rolling median) - 8 x median reading-to-reading change, over steady readings only (baseline moved <= 2.5 C in 15 min). Level changes = held levels (>= 30 min) differing by >= 5 C.
Spread note when Point and Flat assessments differ or totals differ by >= 25 points (SPREAD_WARNING in app.py).

## Charts
- One colour per entity in the app and the PDF (SERIES_COLOURS in both files): Point #2a78d6, Flat #eb6834, PID #1baf7a, Grate #eda100, Hold #e87ba4. Cook/Hold phases in band charts: #e34948 / #4a3aa7. Transfer marker neutral grey #52514e. Sets validated with the dataviz palette validator (light mode, all checks pass).
- Lines break at recording gaps: app break_gaps() inserts empty points; PDF _segments() splits series. Gap = 3 x median sampling interval (or the probe's gap threshold).
- Ideal band 95-105 % shaded green on accumulated rendering charts.

## UI state
- st.session_state.analysed keeps results visible after "Analyse session"; it resets when the uploaded files change (file_id pair in analysed_files).
- Sampling settings use widget keys override_gap and manual_gap_seconds. They are read from session_state at the top of the run, because the widgets are drawn after the summary table.
- html_table() and stat_cards() render wrapping HTML tables and cards. Styles are in TABLE_CSS (bta-* classes, theme-neutral rgba colours).

## History
- v2.8.0: comma decimals in parse_temp; combine_probes averages same-location probes on a shared time grid (each probe held up to its own gap threshold); reference brisket removed; "Pointt" display fix.
- v2.8.1: renamed to Brisket Tenderness Analyzer; layout changes (PDF button on top, side-by-side uploads and roles, settings under the summary, fitted summary table, centred integrity table, wrapping assessment cards); results persist across reruns.
- v2.8.2: cook/hold hours split at the transfer; stepped-program stability and level changes; Point/Flat spread note; PDF restructured (summary first, 9 -> 5 pages, method and assessment scale, file names, °C, readable headers, local generated time).
- 6 Oct 2026, repo cleanup by the user: removed __pycache__, master_timeline.py, INTEGRATION.md, README_v2.6.9.txt, tests/ and old release notes.
- v2.8.3: "Brisket probe analysis" replaced by Rendering by location (accumulated rendering chart with ideal band and milestones, band breakdown); fixed entity colours; gap-aware chart lines; PDF charts redrawn and accumulated chart added; pinned requirements, .gitignore, width="stretch", README rewritten, dead code removed, test_analyzer.py.

## Open issues
1. Band rates give high totals for long holds near 90 C (test cooks score 140-170 %). Needs checking against real cooks with known outcomes; the user is to supply cook files.
2. Lid-open events are detected for grate streams but never shown.
3. .devcontainer/devcontainer.json is only for GitHub Codespaces; harmless.
