# Brisket Tenderness Analyzer v2.8.4

Fixes from the 16 October audit. Each problem below gave wrong results without any warning, or stopped the app.

## Reading times correctly

- **ISO dates** such as `2026-06-01 18:00` are read as year-month-day. Before, they were read as year-day-month: a cook on 1–2 June showed 6 January–6 February with "Cook h 729.90", and a cook that started on the 12th lost everything after midnight.
- **Times with a time zone** (`+02:00`, `Z`, or a zone name such as CEST, CET, MESZ, MEZ, UTC) are converted to your local time. Before, offsets crashed the analysis, and zone names were thrown away.
- **End of summer time.** Times without a zone are read as local time in your browser's time zone. When the clock goes back, the hour that occurs twice is kept in its real order. Before, one copy of that hour was dropped: an overnight cook across the change lost 60 readings and showed 192.9 % instead of 206.2 %. The next change is on 25 October 2026.
- **Separate date and time columns** are combined into one timestamp. Before, only the time column was used, with today's date, and the times wrapped at midnight.
- **Date order is decided per file.** When dates fit both day/month and month/day, the reading that keeps consecutive readings closest together wins, and a note says when dates were read month-first. Before, every value was read day-first on its own.
- A file with times but no dates rolls over to the next day at midnight, with a note.
- Under each upload, a short note says how its times were read: combined columns, month-first dates, time zones converted, or a clock change handled.
- A warning appears if a location's readings span more than 3 days, which usually means the dates were misread.

## Files and columns

- CSV files saved with Windows encoding (for example German exports with "ü" or "°" in a header) now load.
- **Role suggestions**:
  - Setpoint, target, fan, damper, battery and percentage columns are suggested as Ignore.
  - "Setpoint" is no longer suggested as Brisket - Point.
  - A plain "Pit" column is suggested as a cook environment.
  - German names are recognised: "Kern…" as a meat probe, "Garraum" as the cooking chamber.
- **Error messages are plain**: an empty file, a file with no time column, or one with no temperature column now say so directly.
- **The secondary file can't block the run.** A secondary file that can't be read is left out with a message, and the primary file is still analysed.

## Settings

- **Gap override.** Ticking "Override automatic gap detection" now starts the limit at the automatic value for your files, so results don't change until you edit it. Before, it started at 10 seconds, which excluded every reading in typical files and showed 0 %. The override resets when new files are uploaded.
- **Clearer messages.** When every interval is excluded as a gap, the app now says so; before, it said "no time at or above 60 °C". It also notes how many hours of recording gaps were left out.

## PDF

- Column and file names containing `&`, `<` or `>` now appear exactly as written. Before, `<…>` text vanished, and some names crashed the PDF build.

## Other

- The cache of uploaded files is limited to the last 8, so uploads no longer stay in server memory indefinitely.
- `test_analyzer.py` now has 29 tests, up from 14. The new ones cover every date format above, the clock change, combined date and time columns, Windows-encoded files, role suggestions and PDF text.
