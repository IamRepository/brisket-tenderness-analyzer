# Brisket Tenderness Analyzer v2.8.2

## Corrections

- **Cook and hold hours now use the shared transfer time**, the same split used for rendering.
  - Before, each profile was split at its own detected pull. On a cook where Point peaked at 04:00 and Flat at 03:30, the hours didn't match the Cook and Hold percentages, which were split at 03:45.
  - An interval that spans the transfer is now divided at the transfer, as in the rendering calculation.
- **Environment stability no longer penalises a planned stepped program** (for example 80 → 110 → 150 °C).
  - Each reading is compared with a one-hour rolling median, which follows deliberate steps.
  - Readings taken while the cooker is ramping between levels are left out.
  - Scores stay on the same 0–100 scale. A stepped program that previously scored 0/100 now scores 100/100, and poorly controlled temperatures still score low.
  - A new **Level changes** column shows how many setpoint steps were found.
- **A Point/Flat spread is flagged.**
  - When Point and Flat fall in different assessment bands, or differ by 25 percentage points or more, the app and the report say so.
  - The note explains that the whole-brisket figure is their average and describes neither end on its own.

## PDF report

- Page 1 is now a summary:
  - Point, Flat and whole-brisket results;
  - the spread note, when there is one;
  - session start, transfer, last reading, cook time and hold time;
  - the main temperature chart.
- Sections flow on without forced page breaks. A typical report drops from 9 pages to 5.
- The whole-brisket result moved out of the Environment section and into the summary.
- The composite environment section appears only when several hold sensors are averaged. With one sensor per stage, it repeated the environment chart.
- Each location section shows the source once and adds "Rendering split at". Band tables are labelled with their location.
- "Analysed h (≥ 60 °C)" replaces the unexplained "h" column, with a one-line explanation.
- The Method section explains the calculation and includes the assessment scale (< 80 %, 80–95 %, 95–105 %, 105–120 %, > 120 %). Method and configuration move to the end as reference.
- The configuration table lists the uploaded file names.
- Units are shown as °C.
- Table headings are now white on the dark bar; before, they were dark grey and hard to read.
- Environment integrity values are centred.
- "Generated" shows the viewer's local time with its time zone, taken from the browser. It falls back to UTC, labelled as such.

## App

- The configuration table shows file names.
- The environment table shows level changes.
- The spread note appears under the whole-brisket assessment.

## Tests

- New `tests/test_v282.py` covers stability for stepped programs, stepped programs with ramps and noise, poor control, and a constant hold.
