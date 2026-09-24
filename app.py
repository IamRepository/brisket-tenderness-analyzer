from __future__ import annotations

from datetime import datetime, time
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

from brisket_engine import (
    analyse_probe,
    detect_columns,
    prepare_probe_data,
    read_probe_file,
    results_to_excel,
)

st.set_page_config(page_title="Brisket Tenderness", page_icon="🔥", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.5rem; padding-bottom: 3rem;}
    div[data-testid="stMetric"] {background:#fff7ed; border:1px solid #ead7c2; padding:14px; border-radius:14px;}
    .hero {background:linear-gradient(135deg,#18232d,#263b46); color:white; padding:24px 28px; border-radius:18px; margin-bottom:18px;}
    .hero h1 {margin:0; font-size:2.15rem;}
    .hero p {margin:.45rem 0 0; color:#e9ded0;}
    .note {background:#f5f7f3; border-left:5px solid #617d5d; padding:12px 15px; border-radius:8px;}
    </style>
    <div class="hero">
      <h1>🔥 Brisket Tenderness Analyser</h1>
      <p>Upload probe data, split Cook and Hold, and estimate accumulated tenderness.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Analysis settings")
    mode = st.radio(
        "Session type",
        ["Cook only", "Hold / cooldown only", "Split at pull timestamp"],
        index=1,
    )
    max_gap = st.number_input(
        "Maximum accepted gap (seconds)", min_value=1.0, max_value=3600.0, value=10.0, step=1.0,
        help="Intervals longer than this are reported and excluded because the missing temperature is unknown.",
    )
    pull_timestamp = None
    if mode == "Split at pull timestamp":
        pull_date = st.date_input("Pull date", value=datetime.now().date())
        pull_time = st.time_input("Pull time", value=time(12, 0, 0), step=1)
        pull_timestamp = datetime.combine(pull_date, pull_time)

uploaded = st.file_uploader(
    "Upload an Excel or CSV probe export",
    type=["xlsx", "xlsm", "xls", "csv"],
    help="Expected: a timestamp column and one or more Celsius temperature columns.",
)

if uploaded is None:
    st.info("Upload a probe export to begin. The app accepts one probe or multiple temperature columns in the same file.")
    st.markdown(
        """
        <div class="note"><b>Expected timestamp example:</b> 23/05/2026 10:49:35 CEST<br>
        The app handles readings that continue past midnight.</div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

try:
    sheets = read_probe_file(uploaded, uploaded.name)
except Exception as exc:
    st.error(f"The file could not be read: {exc}")
    st.stop()

sheet_name = st.selectbox("Worksheet", list(sheets.keys())) if len(sheets) > 1 else next(iter(sheets))
df = sheets[sheet_name]

try:
    detected_time, detected_temps = detect_columns(df)
except Exception as exc:
    st.error(str(exc))
    st.dataframe(df.head(20), use_container_width=True)
    st.stop()

columns = [str(c) for c in df.columns]
left, right = st.columns([1, 2])
with left:
    timestamp_col = st.selectbox("Timestamp column", columns, index=columns.index(detected_time))
with right:
    default_indices = [columns.index(c) for c in detected_temps if c in columns]
    temperature_cols = st.multiselect(
        "Temperature probe column(s)", columns, default=[columns[i] for i in default_indices]
    )

if not temperature_cols:
    st.warning("Select at least one temperature column.")
    st.stop()

analyse = st.button("Analyse probe data", type="primary", use_container_width=True)
if not analyse:
    with st.expander("Preview uploaded data"):
        st.dataframe(df.head(100), use_container_width=True)
    st.stop()

results = {}
errors = []
for temp_col in temperature_cols:
    try:
        prepared, report = prepare_probe_data(df, timestamp_col, temp_col)
        results[temp_col] = analyse_probe(
            prepared,
            report,
            mode=mode,
            pull_timestamp=pull_timestamp,
            max_gap_seconds=float(max_gap),
        )
    except Exception as exc:
        errors.append(f"{temp_col}: {exc}")

for error in errors:
    st.error(error)
if not results:
    st.stop()

st.success(f"Analysis completed for {len(results)} probe(s).")

for probe_name, result in results.items():
    st.subheader(str(probe_name))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cook rendering", f"{result.cook_rendering:.1%}")
    c2.metric("Hold rendering", f"{result.hold_rendering:.1%}")
    c3.metric("Estimated total", f"{result.total_rendering:.1%}")
    c4.metric("Assessment", result.assessment)

    report = result.parse_report
    st.caption(
        f"{report.valid_rows:,} valid readings • "
        f"{report.invalid_rows:,} invalid • "
        f"{report.duplicate_timestamps:,} duplicates • "
        f"{result.excluded_gap_count:,} excluded gaps • "
        f"{result.analysed_hours:.2f} analysed hours"
    )

    tab_chart, tab_bands, tab_quality = st.tabs(["Temperature chart", "Band calculation", "Data quality"])
    with tab_chart:
        chart_data = result.timeline[["timestamp", "temperature_c", "phase", "status"]].copy()
        chart_data = chart_data[chart_data["status"] == "Analysed"]
        fig = px.line(
            chart_data,
            x="timestamp",
            y="temperature_c",
            color="phase" if chart_data["phase"].nunique() > 1 else None,
            labels={"timestamp": "Time", "temperature_c": "Temperature (°C)", "phase": "Phase"},
            color_discrete_map={"Cook": "#d7652a", "Hold / cooldown": "#5f7d5a"},
        )
        fig.update_layout(height=430, legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)

    with tab_bands:
        display = result.summary.copy()
        display["Duration hours"] = display["Duration hours"].round(3)
        display["Rate per hour"] = display["Rate per hour"].map(lambda x: f"{x:.1%}")
        display["Tenderness contribution"] = display["Tenderness contribution"].map(lambda x: f"{x:.1%}")
        st.dataframe(display, use_container_width=True, hide_index=True)

    with tab_quality:
        q1, q2, q3 = st.columns(3)
        q1.metric("Excluded gap hours", f"{result.excluded_gap_hours:.3f}")
        q2.metric("Below 60°C hours", f"{result.below_model_hours:.3f}")
        q3.metric("Analysed hours", f"{result.analysed_hours:.3f}")
        st.write("First valid reading:", report.first_timestamp)
        st.write("Last valid reading:", report.last_timestamp)

st.divider()
report_bytes = results_to_excel(results)
st.download_button(
    "Download analysis workbook",
    data=report_bytes,
    file_name="brisket_tenderness_analysis.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

st.caption(
    "This is an estimate based on time and internal temperature. Actual brisket tenderness varies. "
    "Use probe tenderness and safe cooking, cooling and holding practices alongside the model."
)
