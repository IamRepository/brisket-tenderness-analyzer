# Brisket Session Analyser 2.2

Version 2.2 is a reliability release.

## Fixes
- Replaced wildcard engine imports with `import brisket_engine as engine`
- Calls engine functions explicitly, including `engine.detect_columns(...)`
- Adds clear error messages for file setup, automatic detection and analysis
- Keeps one-file automatic classification and pull detection

## Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

Use a 10-second maximum gap for second-by-second exports and 70 seconds for one-minute files.
