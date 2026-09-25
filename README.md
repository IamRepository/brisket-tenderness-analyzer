# Brisket Session Analyser 2.1

## Headline change
Upload one probe file. Version 2.1 automatically classifies the session and estimates the pull point.

## Features
- One-file workflow
- Automatic classification: Cook Only, Cook + Hold, Hold Only, Calibration / Hold Test, or Uncertain
- Automatic pull detection for Cook + Hold
- Detection confidence and explanation
- Manual pull override under Advanced options
- Multiple meat-probe support
- Partial-session warning for Cook-only and Hold-only data
- Temperature, accumulated-rendering, band and data-quality views
- Downloadable Excel report
- Built-in one-minute reference brisket

## Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

For second-by-second exports use a maximum gap near 10 seconds. For the built-in one-minute profile use 70 seconds.

## Important
Session classification and pull detection are heuristic estimates. The detected value should be reviewed against cook notes and probe tenderness.
