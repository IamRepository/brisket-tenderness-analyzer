# Brisket Tenderness Analyzer v2.8.7

All 12 items of the audit backlog of 5 November 2026 are fixed.

## Fixed

- **A secondary file that fails to load no longer crashes the app.** Its columns used to stay in Step 2 and cause an error page. They are now added only when the whole file has been read, a failed file is left out with a message, and a failing sensor shows an error instead of stopping the run.
- **Two sensors with the same role and column name stay two sensors.** A "Hold probe" in both files used to overwrite one another, so one vanished from the table, charts, integrity count and PDF.
- **Environment charts draw each sensor as its own line.** Two hold sensors ("Hold top", "Hold bottom") were one line that jumped back. Each is now named by its column. Sensors of one role share its colour; later ones are dashed. The PDF does the same.
- **Times that mix zoned and unzoned values are read correctly.** A few values with an offset used to stop the file with "Mixed timezones detected", or turn the others into UTC. Zoned values are now converted to your time zone and the rest are read as your local time, with a note under the upload.
- **Smoker-only or hold-only files are accepted as the secondary file** even when the readings are hot (above 150 °C) or the first rows are blank. Blank leading rows no longer hide the columns.
- **Upload limits.** Files are limited to 20 MB and 500,000 rows, with a clear message. The PDF is rebuilt only when the files, roles, gap limit or transfer change, so switching dark mode or opening a panel no longer redraws every chart.
- **File-derived names are shown as text.** A column header such as `[link](https://…)` no longer becomes a link or loads an outside picture in labels and messages, and chart series names cannot carry tags.
- **PDF times with a manual transfer outside the readings** no longer go negative ("-1 h 31 min"). The whole session then counts as one phase, and the PDF says the transfer is outside the readings.
- **An empty worksheet** now says "this worksheet is empty. Choose another worksheet or file."

## Setup and cleanup

- Streamlit 1.66.1 with Pillow 13.0.0: the 39 Pillow advisories are gone (pip-audit reports none). The dark-mode switch and all pages were rechecked in a browser.
- Python 3.12 or newer is stated in the README and requirements, and the Codespaces image is now 3.12.
- Removed the unused `band()` function and the unused `manual_gap` parameter.
- Fixed a pandas deprecation warning in the composite hold environment.

## Tests

58 tests (was 35). `test_app.py` runs the whole app in Streamlit's test runner; its five tests fail on v2.8.6 and pass now.

After this release, reboot the app once (Manage app, ⋮, Reboot app): it adds a file, `labels.py`, and changes the pinned libraries.
