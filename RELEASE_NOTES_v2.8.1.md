# Brisket Tenderness Analyzer v2.8.1

## Name

- The app is now called **Brisket Tenderness Analyzer**, replacing Brisket Session Analyser.
- The new name appears in the page title, the browser tab, the PDF report title and page footer, and the README.
- The PDF downloads as `brisket_tenderness_report_YYYY-MM-DD.pdf`, dated by the cook start.

## Layout

- The PDF download button appears above Step 1 once an analysis has run. The sidebar is no longer used.
- Primary and secondary file uploads sit side by side, each with its worksheet picker underneath.
- Step 2 role pickers for the primary and secondary file sit side by side.
- Advanced sampling settings moved under the session detection summary. Changes apply immediately.
- The session detection summary fits the page width:
  - shorter column headings;
  - temperatures to one decimal;
  - the detected pull shown without seconds;
  - text wraps at word boundaries instead of the table scrolling sideways.
  - On a narrow window, the table scrolls inside its own box.
- Environment integrity values are centred in their columns.
- The whole-brisket assessment shows in cards that wrap long text, so assessments such as "Increased risk of mushy or over-rendered texture" are fully visible.

## Behaviour

- Results stay on screen after "Analyse session". Changing a role, a worksheet or a sampling setting updates them right away, and downloading the PDF no longer clears the page.
- Uploading a different file returns to the role step until "Analyse session" is pressed again.

## Notes

- `brisket-analyzer-notes.md` (code notes and release conventions) now ships with the code.
- After uploading a release that changes more than `app.py`, reboot the app on Streamlit Community Cloud (Manage app, ⋮, Reboot app), so the updated helper modules are loaded.
