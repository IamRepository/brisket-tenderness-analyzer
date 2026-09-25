from datetime import datetime
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine


st.set_page_config(
    page_title="Brisket Session Analyser 2.2",
    page_icon="🔥",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem;}
    .hero {
        background: linear-gradient(135deg, #17242d, #29404b);
        color: #ffffff;
        padding: 24px;
        border-radius: 18px;
    }
    .hero p {color: #eadfd3;}
    .hero .attribution {
        color: #d7e4e8;
        font-size: 0.92rem;
        margin-top: 0.85rem;
    }
    div[data-testid="stMetric"] {
        background: #fff7ed;
        border: 1px solid #ead7c2;
        padding: 13px;
        border-radius: 13px;
    }
    .status {
        padding: 13px;
        border-left: 5px solid #5f7d5a;
        background: #f4f7f1;
        border-radius: 8px;
    }
    </style>

    <div class="hero">
      <h1>🔥 Brisket Session Analyser 2.2</h1>
      <p>Upload one probe file. Automatic session classification and pull detection are built in.</p>
      <p class="attribution">
        Tenderness methodology inspired by the time-temperature rendering and hot-hold concepts
        shared by Steve Gow. This is an independent software implementation and is not affiliated
        with, endorsed by, or maintained by Steve Gow.
        This is developed by Imran Abdul-Majid
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.sidebar.header("Analysis settings")
max_gap = st.sidebar.number_input(
    "Maximum accepted gap (seconds)",
    1.0,
    3600.0,
    10.0,
    1.0,
    help="Use 10 for second-by-second exports and 70 for one-minute data.",
)

source = st.segmented_control(
    "Data source",
    ["Upload probe file", "Try reference brisket"],
    default="Upload probe file",
)


@st.cache_data
def load_bytes(data, name):
    return engine.read_file(BytesIO(data), name)


try:
    if source == "Try reference brisket":
        df = engine.demo_data()
        timestamp_col = "timestamp"
        probes = ["Average Probe Temperature (°C)"]
        effective_gap = max(max_gap, 70.0)
        st.info(
            "The reference profile uses one-minute readings; "
            "the effective gap threshold is at least 70 seconds."
        )
    else:
        upload = st.file_uploader(
            "Upload Excel or CSV probe data",
            type=["xlsx", "xlsm", "xls", "csv"],
        )
        if not upload:
            st.info("Upload a probe file to begin.")
            st.stop()

        sheets = load_bytes(upload.getvalue(), upload.name)
        selected_sheet = st.selectbox("Worksheet", list(sheets))
        df = sheets[selected_sheet]

        detected_timestamp, detected_probes = engine.detect_columns(df)
        columns = list(df.columns)

        left, right = st.columns([1, 2])
        timestamp_col = left.selectbox(
            "Timestamp column",
            columns,
            index=columns.index(detected_timestamp),
        )
        probes = right.multiselect(
            "Meat probe column(s)",
            columns,
            default=detected_probes,
        )
        if not probes:
            st.warning("Select at least one meat probe.")
            st.stop()

        effective_gap = float(max_gap)
except Exception as exc:
    st.error(f"File setup failed: {exc}")
    st.stop()

prepared = {}
detections = {}

try:
    for probe in probes:
        valid, report = engine.prepare(df, timestamp_col, probe)
        prepared[probe] = (valid, report)
        detections[probe] = engine.classify_session(valid)
except Exception as exc:
    st.error(f"Automatic detection failed: {exc}")
    st.stop()

st.subheader("Automatic detection")
rows = []
for probe, detection in detections.items():
    rows.append(
        {
            "Probe": probe,
            "Session type": detection.session_type,
            "Confidence": f"{detection.confidence}%",
            "Detected pull": detection.pull_timestamp,
            "Peak °C": round(detection.peak_temperature, 1),
            "Average °C": round(detection.average_temperature, 1),
            "Minimum °C": round(detection.minimum_temperature, 1),
        }
    )

st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

primary = detections[probes[0]]
override = None

if primary.session_type == "Cook + Hold" and primary.pull_timestamp is not None:
    st.markdown(
        f"""
        <div class="status">
          <b>Detected pull:</b> {primary.pull_timestamp:%d/%m/%Y %H:%M:%S}
          &nbsp; <b>Confidence:</b> {primary.confidence}%<br>
          {primary.reason}
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Advanced: override detected pull time"):
        if st.checkbox("Override detected pull time"):
            left, right = st.columns(2)
            date = left.date_input("Pull date", primary.pull_timestamp.date())
            selected_time = right.time_input(
                "Pull time",
                primary.pull_timestamp.time(),
                step=60,
            )
            override = datetime.combine(date, selected_time)
else:
    st.info(f"Detected session: {primary.session_type}. {primary.reason}")

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()

results = {}
try:
    for probe, (valid, report) in prepared.items():
        detection = detections[probe]
        local_override = (
            override
            if override is not None and detection.session_type == "Cook + Hold"
            else None
        )
        results[str(probe)] = engine.analyse(
            valid,
            report,
            detection,
            local_override,
            effective_gap,
        )
except Exception as exc:
    st.error(f"Analysis failed: {exc}")
    st.stop()

st.subheader("Results")
for name, result in results.items():
    with st.expander(name, expanded=len(results) == 1):
        if not result.complete:
            st.warning(
                "Partial session detected. The percentage is the recorded phase "
                "contribution, not a complete final-tenderness assessment."
            )

        a, b, c, d = st.columns(4)
        a.metric("Cook contribution", f"{result.cook:.1%}")
        b.metric("Hold contribution", f"{result.hold:.1%}")
        c.metric("Recorded total", f"{result.total:.1%}")
        d.metric(
            "Assessment",
            result.assessment if result.complete else "Partial session",
        )

        tabs = st.tabs(
            ["Temperature", "Accumulated rendering", "Band calculation", "Data quality"]
        )

        with tabs[0]:
            chart = result.timeline[result.timeline.Status == "Analysed"]
            fig = px.line(
                chart,
                x="Timestamp",
                y="Temperature °C",
                color="Phase" if chart.Phase.nunique() > 1 else None,
                color_discrete_map={
                    "Cook": "#d7652a",
                    "Hold / cooldown": "#5f7d5a",
                },
            )
            st.plotly_chart(fig, use_container_width=True)

        with tabs[1]:
            fig = px.line(
                result.timeline,
                x="Timestamp",
                y="Accumulated rendering",
            )
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(fig, use_container_width=True)

        with tabs[2]:
            summary = result.summary[result.summary["Duration hours"] > 0].copy()
            summary["Duration hours"] = summary["Duration hours"].round(3)
            summary["Rate per hour"] = summary["Rate per hour"].map(
                lambda x: f"{x:.1%}"
            )
            summary["Tenderness contribution"] = summary[
                "Tenderness contribution"
            ].map(lambda x: f"{x:.1%}")
            st.dataframe(summary, hide_index=True, use_container_width=True)

        with tabs[3]:
            q1, q2, q3 = st.columns(3)
            q1.metric("Analysed hours", f"{result.analysed_hours:.3f}")
            q2.metric("Excluded gap hours", f"{result.excluded_gap_hours:.3f}")
            q3.metric("Below 60°C hours", f"{result.below_model_hours:.3f}")
            st.write(
                "Valid / invalid / duplicates:",
                result.report.valid_rows,
                result.report.invalid_rows,
                result.report.duplicate_timestamps,
            )

st.download_button(
    "Download analysis workbook",
    engine.to_excel(results),
    "brisket_session_analysis_v2.2.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

st.caption(
    "Automatic classification and pull detection are estimates. Review the detected "
    "pull point and use probe tenderness plus safe food handling alongside the model."
)
