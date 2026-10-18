# Brisket Tenderness Analyzer: code notes

Last updated 18 October 2026 for v2.8.5. Repo: github.com/IamRepository/brisket-tenderness-analyzer (public). Streamlit app on Streamlit Community Cloud, deployed from main.

## Name and release conventions
- Product name: Brisket Tenderness Analyzer (US spelling). It replaced "Brisket Session Analyser" in v2.8.1.
- Where the name appears: app.py (APP_NAME, used for page_title and st.title), pdf_report.py (report title, page footer), README.md title, release notes titles.
- Workflow (from 16 Oct 2026): Claude has write access and commits and pushes releases straight to main (the user's choice; no pull requests). A push to main goes live on Streamlit Cloud. Before pushing: run the tests and an end-to-end app run. Commit message starts with the version, e.g. "v2.8.4: ...".
- Each release updates APP_VERSION, adds RELEASE_NOTES_vX.Y.Z.md (removing the previous one) and updates this file.
- After a release that changes more than app.py, the user reboots the app (Manage app, ⋮, Reboot app). Otherwise Streamlit can keep the previous brisket_engine.py in memory; v2.8.0 failed with AttributeError on combine_probes until a reboot.
- Before 16 Oct, releases were zips ("Brisket Tenderness Analyzer vX.Y.Z.zip") uploaded by hand; subfolders did not survive that upload, so files are kept at the top level.

## Files
- app.py: whole UI and orchestration. Title, PDF button slot, Step 1 uploads (two columns), Step 2 role pickers (two columns), "Analyse session" -> consolidate Point/Flat -> derive transfer -> environment segmentation -> session summary, advanced sampling settings, environment integrity, whole-brisket cards and spread note, probe comparison (table + chart), environment analysis (table + chart), Rendering by location (accumulated chart + milestone lines + band breakdown per location). All tables are html_table().
- brisket_engine.py: parsing (parse_timestamps/parse_ts, parse_temp, read_file with merge_date_time_columns, detect_columns, prepare), classify_session (Cook + Hold / Hold Only / Calibration / Cook Only / Uncertain, pull = smoothed peak), analyse (10 temperature bands from 60 C, ZONE_RATES per hour, cook/hold split at pull, gaps above max_gap excluded; Result.timeline holds per-interval and accumulated rendering), assess (thresholds 80/95/105/120 %), combine_probes.
- pit_engine.py: role constants (Point, Flat, Cook PID, Cook Grate, Hold Env, Ignore), classify_columns by name hints then shape, prepare, analyse (avg/min/max, steady_stability score and level_changes, lid-drop events for grate).
- pdf_report.py: ReportLab A4 portrait report. Order: summary (results, spread callout, session times, main chart), session details (timing, temperatures, rendering by profile, accumulated rendering chart), environment (composite only with several hold sensors), one section per location (metrics + band table), method + assessment scale, configuration. Table text is Paragraphs, so colour/alignment/bold must come from paragraph styles (HeadCell, CellC) or <b> markup, not TableStyle. _chart() draws its own lines, grid, legend and labels (no LinePlot).
- test_analyzer.py: 35 tests (date formats, time zones, clock change, date+time columns, encodings, roles, combine_probes, session and short rests, rendering calculation and transfer split, speed, assessment scale, stability, PDF text escaping). Run: python -m unittest test_analyzer.
- requirements.txt: exact versions tested. .gitignore: keeps __pycache__ out.

## Model
Rendering = sum over intervals of hours x band rate, using the temperature at the start of each interval (analyse() is vectorised with numpy; identical to the old per-row loop). Whole-brisket = mean of canonical Point and Flat totals. Transfer = the user's manual setting (transfer_mode / transfer_date / transfer_clock in session_state, read by manual_transfer_time()) or else the median of Cook + Hold pull times (derive_transfer_time). When a transfer is known, analyse() and phase_stats() split every probe at it whatever its session type; complete = readings on both sides and (Cook + Hold or a session transfer).
classify_session: smoothing window 15 min and start/end levels over 10 min, measured in time (SMOOTH_WINDOW_S, EDGE_WINDOW_S); start/end use raw readings. Cook + Hold = rise >= 25, fall >= 8, peak not in the first 15 % and >= 30 min of readings after the peak (MIN_HOLD_AFTER_PEAK_S).
Stability = 100 - 4 x std(deviation from 60-min rolling median) - 8 x median reading-to-reading change, over steady readings only (baseline moved <= 2.5 C in 15 min). Level changes = held levels (>= 30 min) differing by >= 5 C.
Spread note when Point and Flat assessments differ or totals differ by >= 25 points (SPREAD_WARNING in app.py).

## Timestamps (v2.8.4)
- Every timestamp is time-zone-aware in one zone: the viewer's browser zone (VIEW_TZ in app.py, from st.context.timezone; UTC if unknown). Never mix naive and aware values.
- parse_timestamps(series, tz) -> (aware series, notes). Per column: Excel serials; native datetimes (aware -> converted, naive -> localised); time-only columns roll over at midnight; strings: zone words (CEST/CET/MESZ/MEZ/BST/UTC/GMT, Z) -> offsets; date order decided once per column (_date_order: ISO, first field > 12 = day-first, second > 12 = month-first, else the reading with the smaller total step wins, tie = day-first); then one format for the whole column (guess_datetime_format, fallback format='mixed').
- _localise: naive local times are localised with ambiguous flags from file order (first pass of the repeated autumn hour = summer time, second = winter time); spring gaps shift forward.
- read_file decodes UTF-8 / cp1252 / Latin-1 and merges a date-only + time-only column pair into COMBINED_TIME_COLUMN.
- pdf_report: table cells go through _cell(), which XML-escapes file text; use the Markup class for intentional <b> markup.

## Charts
- One colour per entity in the app and the PDF (SERIES_COLOURS in both files): Point #2a78d6, Flat #eb6834, PID #1baf7a, Grate #eda100, Hold #e87ba4. Cook/Hold phases in band charts: #e34948 / #4a3aa7. Transfer marker neutral grey #52514e. Sets validated with the dataviz palette validator (light mode, all checks pass).
- Lines break at recording gaps: app break_gaps() inserts empty points; PDF _segments() splits series. Gap = 3 x median sampling interval (or the probe's gap threshold).
- Ideal band 95-105 % shaded green on accumulated rendering charts.

## UI state
- Dark mode: theme_switch() beside the title (copied from BBQ Chart to Data): a toggle that clicks Streamlit's ⋮ menu Light/Dark item, then Rerun. IS_DARK from st.context.theme.type picks the colour set (SERIES_COLOURS, PHASE_COLOURS, MARKER_COLOUR, IDEAL_FILL/TEXT, FILE_BOX). Dark series: Point #3987e5, Flat #d95926, PID #199e70, Grate #c98500, Hold #d55181; phases #e66767 / #9085e9 (validated --mode dark per chart). PDF stays light.
- Transfer setting resets with new files, like the gap override (override_files key).
- st.session_state.analysed keeps results visible after "Analyse session"; it resets when the uploaded files change (file_id pair in analysed_files).
- Sampling settings use widget keys override_gap and manual_gap_seconds. They are read from session_state at the top of the run, because the widgets are drawn after the summary table.
- html_table() and stat_cards() render wrapping HTML tables and cards. Styles are in TABLE_CSS (bta-* classes, theme-neutral rgba colours).

## History
- v2.8.0: comma decimals in parse_temp; combine_probes averages same-location probes on a shared time grid (each probe held up to its own gap threshold); reference brisket removed; "Pointt" display fix.
- v2.8.1: renamed to Brisket Tenderness Analyzer; layout changes (PDF button on top, side-by-side uploads and roles, settings under the summary, fitted summary table, centred integrity table, wrapping assessment cards); results persist across reruns.
- v2.8.2: cook/hold hours split at the transfer; stepped-program stability and level changes; Point/Flat spread note; PDF restructured (summary first, 9 -> 5 pages, method and assessment scale, file names, °C, readable headers, local generated time).
- 6 Oct 2026, repo cleanup by the user: removed __pycache__, master_timeline.py, INTEGRATION.md, README_v2.6.9.txt, tests/ and old release notes.
- v2.8.3: "Brisket probe analysis" replaced by Rendering by location (accumulated rendering chart with ideal band and milestones, band breakdown); fixed entity colours; gap-aware chart lines; PDF charts redrawn and accumulated chart added; pinned requirements, .gitignore, width="stretch", README rewritten, dead code removed, test_analyzer.py.
- 16 Oct 2026: Claude connected to the repo with write access; releases now pushed straight to main.
- 16 Oct 2026: deep audit of v2.8.3 (16 findings).
- v2.8.4: audit fixes 1-6, 8, 12, 13, 15: ISO dates, time zones and offsets, end-of-summer-time clock change, separate date/time columns, per-column date order, Windows-encoded CSVs, role suggestions (setpoint/fan/battery ignored, Pit), gap override default, clearer errors, secondary file can't block the run, PDF text escaping, upload cache capped; 29 tests.
- v2.8.5: dark mode toggle; vectorised rendering (24 h at 2 s: 80 s -> 2.5 s); short rests detected by time after the peak; more accurate pull; manual smoker-to-hold transfer with a hint when the cook environment stops early; the transfer splits every probe; 35 tests.

## Open issues
1. Fahrenheit files are read as Celsius (readings above 150 dropped); no unit detection.
2. Composite hold environment joins on exact timestamps (same interleave issue fixed for probes in 2.8.0); rarely shown.
3. Files with only elapsed time (no dates or times) are rejected.
4. Very short rests (under 30 min, or a fall under 8 C) still read as Cook Only; the manual transfer covers them.
5. Band rates give high totals for long holds near 90 C (test cooks score 140-200 %). Needs checking against real cooks with known outcomes; the user is to supply cook files.
6. Lid-open events are detected for grate streams but never shown.
7. .devcontainer/devcontainer.json is only for GitHub Codespaces; harmless.
