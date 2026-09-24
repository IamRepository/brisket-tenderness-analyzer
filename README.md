# Brisket Session Analyser 2.0

## Version 2 improvements
- Single combined file or separate Cook and Hold uploads
- Built-in reference brisket reconstructed from the supplied Typhur screenshot
- Multiple meat probes, comparison dashboard and Flat/Point-ready workflow
- Hold-only and Cook-only results labelled as partial contributions
- Only non-zero bands shown, with three-decimal-hour precision
- Accumulated rendering chart
- Data-quality reporting and configurable gap threshold
- Downloadable Excel report
- Automatic exclusion of pit, ambient and target columns from probe detection

## Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

For minute-by-minute demo data, use a maximum gap above 60 seconds, e.g. 70 seconds. For second-by-second exports, use 10 seconds.
