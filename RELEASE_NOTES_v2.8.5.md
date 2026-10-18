# Brisket Tenderness Analyzer v2.8.5

## Dark mode

- A **Dark mode** switch sits beside the title, as in BBQ Chart to Data. It changes the theme straight away, and nothing on the page is lost. The same choice is in the ⋮ menu, which also has "System" to follow your computer's setting.
- In dark mode, charts use their own darker set of colours, each checked for colour-blind separation. The Step 2 file headers, the transfer marker and the ideal band follow the theme too.
- The PDF report stays light, for printing.

## Speed

- **Large logs are fast.** The rendering calculation now handles all readings at once instead of one row at a time. A 24-hour log at 2-second intervals with four meat probes and a grate probe takes about 2.5 seconds per run, down from about 80. The results are identical to v2.8.4.
- **Less repeated work.**
  - Raw probes are no longer analysed separately, since those results were discarded.
  - Each location is analysed once.
  - Each file's time column is read once.

## Cook and hold detection

- **A short rest is now recognised** as a cook and hold. It needs at least 30 minutes of readings after the peak, with a fall of 8 °C or more. Before, the peak also had to fall within the first 90 % of the session, so a 12-hour cook with a 1-hour rest was labelled "Cook Only" and shown as a partial session.
- **The detected pull is more accurate.** The peak is found with a 15-minute window and the start and end temperatures over 10 minutes, measured in time rather than in number of readings. Detected pulls now land within one reading of the true peak; before, they could be up to 30 minutes early. Cook and Hold totals don't change, but the split between them can move slightly.

## Smoker-to-hold transfer

- **New setting:** "Smoker-to-hold transfer" under the session summary. Choose **Automatic** (the brisket peak) or **Set manually** (date and time). Switching to manual starts from the transfer in use.
- **The info box says where the time came from:** estimated from the peak, or set by you.
- **The app hints when the estimate may be late.** If the cook environment readings stop more than 10 minutes before the estimated transfer, a hint suggests that time instead. The brisket keeps rising for a while after it leaves the smoker, so the peak comes after the move.
- **The transfer applies to the whole session.** Every probe's readings before it count as Cook and after it as Hold, including a probe whose own session looks like cook-only or hold-only. A result is "complete" when there are readings on both sides of the transfer.
- **Cook environment readings** are used up to the transfer, and **Hold environment readings** from it.
- **The PDF** marks a transfer you set by hand.
- **New files reset the setting** to Automatic.

## Tests

- 35 tests, up from 29. They cover:
  - short rests at 1, 5 and 15-minute logging;
  - an interval-by-interval check of the rendering calculation;
  - an interval split at the transfer;
  - a hand-set transfer on a cook-only profile;
  - a speed check on a 43,000-reading log.
