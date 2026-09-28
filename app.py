from datetime import datetime
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit


APP_VERSION = "2.4.2"
MEAT_ROLES = ("🥩 Point", "🥩 Flat", "🍖 Other Meat")
ENVIRONMENT_ROLES = ("🌡 Grate", "🔥 PID")


st.set_page_config(
    page_title=f"Brisket Session Analyser {APP_VERSION}",
    page_icon="🔥",
    layout="wide",
)

st.title(f"🔥 Brisket Session Analyser {APP_VERSION}")
st.caption(
    "Based on Steve Gow's brisket rendering and hot-hold methodology. "
    "Independent implementation; not affiliated with or endorsed by Steve Gow."
)

st.sidebar.header("Settings")
max_gap = st.sidebar.number_input(
    "Maximum accepted gap (seconds)",
    min_value=1.0,
    max_value=3600.0,
    value=10.0,
    step=1.0,
    help=(
        "Intervals longer than this are treated as recording gaps and excluded. "
        "Use a value above the normal sampling interval."
    ),
)

source = st.segmented_control(
    "Data source",
    ["Upload file", "Try reference brisket"],
    default="Upload file",
)


@st.cache_data
def load_file(data: bytes, name: str):
    return engine.read_file(BytesIO(data), name)


def source_label(file_role: str, column: str) -> str:
    return f"{file_role} / {column}"


def unique_profile_name(role: str, file_role: str, column: str, existing: dict) -> str:
    base = role
    if base not in existing:
        return base

    candidate = f"{role} — {file_role} / {column}"
    if candidate not in existing:
        return candidate

    number = 2
    while f"{candidate} ({number})" in existing:
        number += 1
    return f"{candidate} ({number})"


def duration_hours(start, end) -> float:
    if start is None or end is None:
        return 0.0
    return max((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 3600.0, 0.0)


def phase_statistics(result):
    analysed = result.timeline[result.timeline["Status"] == "Analysed"].copy()
    if analysed.empty:
        return {
            "cook_hours": 0.0,
            "hold_hours": 0.0,
            "average_cook": None,
            "average_hold": None,
        }

    cook = analysed[analysed["Phase"].str.contains("Cook", na=False)]
    hold = analysed[analysed["Phase"].str.contains("Hold", na=False)]

    cook_seconds = float(cook["Elapsed seconds"].clip(lower=0).sum())
    hold_seconds = float(hold["Elapsed seconds"].clip(lower=0).sum())

    def weighted_average(frame):
        if frame.empty:
            return None
        weights = frame["Elapsed seconds"].clip(lower=0)
        if float(weights.sum()) <= 0:
            return float(frame["Temperature °C"].mean())
        return float((frame["Temperature °C"] * weights).sum() / weights.sum())

    return {
        "cook_hours": cook_seconds / 3600.0,
        "hold_hours": hold_seconds / 3600.0,
        "average_cook": weighted_average(cook),
        "average_hold": weighted_average(hold),
    }


# -----------------------------------------------------------------------------
# Step 1: Upload and inspect source files
# -----------------------------------------------------------------------------
if source == "Try reference brisket":
    primary_name = "Reference brisket"
    df = engine.demo_data()
    timestamp_col = "timestamp"
    classifications = pd.DataFrame(
        [
            {
                "Column": "Average Probe Temperature (°C)",
                "Suggested role": "🍖 Other Meat",
                "Confidence": 99,
                "File": "Reference",
            }
        ]
    )
    extra_df = None
    extra_timestamp = None
    optional_name = None
    effective_gap = max(float(max_gap), 70.0)
else:
    st.subheader("Step 1: Upload temperature files")
    primary = st.file_uploader(
        "Primary temperature file",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="primary",
    )
    optional = st.file_uploader(
        "Secondary temperature file (optional)",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="optional",
    )

    if not primary:
        st.info("Upload a primary temperature file to begin.")
        st.stop()

    primary_name = primary.name
    sheets = load_file(primary.getvalue(), primary.name)
    sheet = st.selectbox("Primary worksheet", list(sheets), key="primary_sheet")
    df = sheets[sheet]
    timestamp_col, _ = engine.detect_columns(df)

    classifications = pit.classify_columns(df, timestamp_col)
    classifications["File"] = "Primary"

    extra_df = None
    extra_timestamp = None
    optional_name = None

    if optional:
        optional_name = optional.name
        optional_sheets = load_file(optional.getvalue(), optional.name)
        optional_sheet = st.selectbox(
            "Secondary worksheet",
            list(optional_sheets),
            key="secondary_sheet",
        )
        extra_df = optional_sheets[optional_sheet]
        extra_timestamp, _ = engine.detect_columns(extra_df)
        extra_classifications = pit.classify_columns(extra_df, extra_timestamp)
        extra_classifications["File"] = "Secondary"
        classifications = pd.concat(
            [classifications, extra_classifications],
            ignore_index=True,
        )

    effective_gap = float(max_gap)


# -----------------------------------------------------------------------------
# Step 2: Map every detected column to a semantic role
# -----------------------------------------------------------------------------
st.subheader("Step 2: Assign probe roles")
st.caption(
    "Tell the analyser how each temperature channel was used. "
    "Point, Flat and Other Meat are analysed independently."
)

roles = {}

for file_role, colour, icon in (
    ("Primary", "#eaf3ff", "🟦"),
    ("Secondary", "#edf9ef", "🟩"),
    ("Reference", "#fff6df", "🟨"),
):
    group = classifications[classifications["File"] == file_role]
    if group.empty:
        continue

    file_name = (
        primary_name
        if file_role in ("Primary", "Reference")
        else optional_name
    )

    st.markdown(
        f"""
        <div style="background:{colour}; padding:12px 16px; border-radius:10px;
                    margin:14px 0 8px 0; border:1px solid #d8dee6;">
          <strong>{icon} {file_role.upper()} FILE</strong><br>
          <span style="font-size:0.9rem; color:#4d5966;">{file_name or file_role}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for index, row in group.iterrows():
        key = f"{file_role}_{row['Column']}"
        suggested = row["Suggested role"]
        suggested_index = pit.ROLES.index(suggested) if suggested in pit.ROLES else 0
        roles[key] = st.selectbox(
            f"{row['Column']} ({row['Confidence']}% suggested confidence)",
            pit.ROLES,
            index=suggested_index,
            key=f"role_{key}",
        )


st.markdown("#### Detected configuration")
configuration_rows = []
for _, row in classifications.iterrows():
    file_role = row["File"]
    key = f"{file_role}_{row['Column']}"
    role = roles[key]
    if role != "🚫 Ignore":
        configuration_rows.append(
            {
                "Role": role,
                "Source": source_label(file_role, str(row["Column"])),
            }
        )

if configuration_rows:
    st.dataframe(
        pd.DataFrame(configuration_rows),
        hide_index=True,
        use_container_width=True,
    )
else:
    st.warning("All detected temperature columns are currently set to Ignore.")

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()


# -----------------------------------------------------------------------------
# Step 3: Analyse every selected role
# -----------------------------------------------------------------------------
meat_results = {}
pit_results = {}
meat_sources = {}

for _, row in classifications.iterrows():
    file_role = row["File"]
    column = row["Column"]
    key = f"{file_role}_{column}"
    role = roles[key]

    if role == "🚫 Ignore":
        continue

    active_df = df if file_role in ("Primary", "Reference") else extra_df
    active_timestamp = (
        timestamp_col
        if file_role in ("Primary", "Reference")
        else extra_timestamp
    )

    if role in MEAT_ROLES:
        valid, report = engine.prepare(active_df, active_timestamp, column)
        detection = engine.classify_session(valid)
        profile_name = unique_profile_name(role, file_role, str(column), meat_results)
        meat_results[profile_name] = engine.analyse(
            valid,
            report,
            detection,
            max_gap=effective_gap,
        )
        meat_sources[profile_name] = source_label(file_role, str(column))

    elif role in ENVIRONMENT_ROLES:
        prepared = pit.prepare(active_df, active_timestamp, column)
        environment_name = f"{role} — {file_role} / {column}"
        pit_results[environment_name] = pit.analyse(prepared, role)

if not meat_results:
    st.error(
        "At least one column must be classified as Point, Flat or Other Meat."
    )
    st.stop()


# -----------------------------------------------------------------------------
# Step 4: Restore detection summary and session metrics
# -----------------------------------------------------------------------------
st.subheader("Session detection summary")
summary_rows = []

for profile_name, result in meat_results.items():
    detection = result.detection
    phase_stats = phase_statistics(result)
    summary_rows.append(
        {
            "Profile": profile_name,
            "Source": meat_sources[profile_name],
            "Session type": detection.session_type,
            "Confidence": f"{detection.confidence}%",
            "Detected pull": detection.pull_timestamp,
            "Peak °C": round(detection.peak_temperature, 1),
            "Average °C": round(detection.average_temperature, 1),
            "Minimum °C": round(detection.minimum_temperature, 1),
            "Cook hours": round(phase_stats["cook_hours"], 2),
            "Hold hours": round(phase_stats["hold_hours"], 2),
            "Average cook °C": (
                round(phase_stats["average_cook"], 1)
                if phase_stats["average_cook"] is not None
                else None
            ),
            "Average hold °C": (
                round(phase_stats["average_hold"], 1)
                if phase_stats["average_hold"] is not None
                else None
            ),
        }
    )

st.dataframe(
    pd.DataFrame(summary_rows),
    hide_index=True,
    use_container_width=True,
)


# -----------------------------------------------------------------------------
# Step 5: Comparison dashboard and combined meat chart
# -----------------------------------------------------------------------------
st.subheader("Meat-profile comparison")
comparison_rows = []
combined_meat_frames = []

for profile_name, result in meat_results.items():
    comparison_rows.append(
        {
            "Profile": profile_name,
            "Source": meat_sources[profile_name],
            "Cook contribution": result.cook,
            "Hold contribution": result.hold,
            "Recorded total": result.total,
            "Assessment": result.assessment if result.complete else "Partial session",
            "Analysed hours": result.analysed_hours,
        }
    )

    chart_data = result.timeline[result.timeline["Status"] == "Analysed"].copy()
    chart_data["Profile"] = profile_name
    combined_meat_frames.append(
        chart_data[["Timestamp", "Temperature °C", "Profile", "Phase"]]
    )

comparison_df = pd.DataFrame(comparison_rows)
st.dataframe(
    comparison_df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Cook contribution": st.column_config.NumberColumn(format="%.1f%%"),
        "Hold contribution": st.column_config.NumberColumn(format="%.1f%%"),
        "Recorded total": st.column_config.NumberColumn(format="%.1f%%"),
        "Analysed hours": st.column_config.NumberColumn(format="%.2f"),
    },
)

all_meat = pd.concat(combined_meat_frames, ignore_index=True)
meat_figure = px.line(
    all_meat,
    x="Timestamp",
    y="Temperature °C",
    color="Profile",
    line_dash="Phase" if all_meat["Phase"].nunique() > 1 else None,
    title="All classified meat probes",
)
st.plotly_chart(meat_figure, use_container_width=True)


# -----------------------------------------------------------------------------
# Step 6: Detailed results for every meat probe
# -----------------------------------------------------------------------------
for profile_name, result in meat_results.items():
    with st.expander(
        f"{profile_name} — {meat_sources[profile_name]}",
        expanded=len(meat_results) == 1,
    ):
        detection = result.detection
        phase_stats = phase_statistics(result)

        top1, top2, top3, top4 = st.columns(4)
        top1.metric("Session", detection.session_type)
        top2.metric("Confidence", f"{detection.confidence}%")
        top3.metric(
            "Detected pull",
            detection.pull_timestamp.strftime("%d/%m/%Y %H:%M")
            if detection.pull_timestamp is not None
            else "Not detected",
        )
        top4.metric("Peak temperature", f"{detection.peak_temperature:.1f}°C")

        metric1, metric2, metric3, metric4 = st.columns(4)
        metric1.metric("Cook contribution", f"{result.cook:.1%}")
        metric2.metric("Hold contribution", f"{result.hold:.1%}")
        metric3.metric("Recorded total", f"{result.total:.1%}")
        metric4.metric(
            "Assessment",
            result.assessment if result.complete else "Partial session",
        )

        metric5, metric6, metric7, metric8 = st.columns(4)
        metric5.metric("Cook duration", f"{phase_stats['cook_hours']:.2f} h")
        metric6.metric("Hold duration", f"{phase_stats['hold_hours']:.2f} h")
        metric7.metric(
            "Average cook temperature",
            f"{phase_stats['average_cook']:.1f}°C"
            if phase_stats["average_cook"] is not None
            else "N/A",
        )
        metric8.metric(
            "Average hold temperature",
            f"{phase_stats['average_hold']:.1f}°C"
            if phase_stats["average_hold"] is not None
            else "N/A",
        )

        tabs = st.tabs(
            [
                "Temperature",
                "Accumulated rendering",
                "Band calculation",
                "Data quality",
            ]
        )

        with tabs[0]:
            chart = result.timeline[result.timeline["Status"] == "Analysed"]
            figure = px.line(
                chart,
                x="Timestamp",
                y="Temperature °C",
                color="Phase" if chart["Phase"].nunique() > 1 else None,
            )
            st.plotly_chart(figure, use_container_width=True)

        with tabs[1]:
            figure = px.line(
                result.timeline,
                x="Timestamp",
                y="Accumulated rendering",
            )
            figure.update_yaxes(tickformat=".0%")
            st.plotly_chart(figure, use_container_width=True)

        with tabs[2]:
            band_summary = result.summary[
                result.summary["Duration hours"] > 0
            ].copy()
            band_summary["Duration hours"] = band_summary["Duration hours"].round(3)
            band_summary["Rate per hour"] = band_summary["Rate per hour"].map(
                lambda value: f"{value:.1%}"
            )
            band_summary["Tenderness contribution"] = band_summary[
                "Tenderness contribution"
            ].map(lambda value: f"{value:.1%}")
            st.dataframe(
                band_summary,
                hide_index=True,
                use_container_width=True,
            )

        with tabs[3]:
            quality1, quality2, quality3 = st.columns(3)
            quality1.metric("Analysed hours", f"{result.analysed_hours:.3f}")
            quality2.metric("Excluded gap hours", f"{result.excluded_gap_hours:.3f}")
            quality3.metric("Below 60°C hours", f"{result.below_model_hours:.3f}")
            st.write(
                "Valid / invalid / duplicates:",
                result.report.valid_rows,
                result.report.invalid_rows,
                result.report.duplicate_timestamps,
            )


# -----------------------------------------------------------------------------
# Step 7: Environmental analysis and overlay
# -----------------------------------------------------------------------------
if pit_results:
    st.subheader("Cooking-environment analysis")

    for environment_name, result in pit_results.items():
        with st.expander(environment_name, expanded=True):
            a, b, c, d = st.columns(4)
            a.metric("Average", f"{result.average:.1f}°C")
            b.metric("Minimum", f"{result.minimum:.1f}°C")
            c.metric("Maximum", f"{result.maximum:.1f}°C")
            d.metric("Stability", f"{result.stability_score:.0f}/100")

            st.plotly_chart(
                px.line(
                    result.timeline,
                    x="timestamp",
                    y="temperature_c",
                    labels={"temperature_c": f"{result.role} °C"},
                ),
                use_container_width=True,
            )

            if len(result.lid_events):
                st.write("Possible lid-open events")
                st.dataframe(
                    result.lid_events,
                    hide_index=True,
                    use_container_width=True,
                )

    overlay_frames = []

    for profile_name, result in meat_results.items():
        meat_frame = result.timeline[["Timestamp", "Temperature °C"]].copy()
        meat_frame.columns = ["timestamp", "Temperature °C"]
        meat_frame["Temperature source"] = profile_name
        overlay_frames.append(meat_frame)

    for environment_name, result in pit_results.items():
        environment_frame = result.timeline[["timestamp", "temperature_c"]].copy()
        environment_frame.columns = ["timestamp", "Temperature °C"]
        environment_frame["Temperature source"] = environment_name
        overlay_frames.append(environment_frame)

    full_overlay = pd.concat(overlay_frames, ignore_index=True)
    full_overlay.sort_values("timestamp", inplace=True)

    st.subheader("Meat and cooking-environment overlay")
    st.plotly_chart(
        px.line(
            full_overlay,
            x="timestamp",
            y="Temperature °C",
            color="Temperature source",
        ),
        use_container_width=True,
    )


st.caption(
    "Pit stability and event detections are analytical estimates, "
    "not manufacturer-provided Weber metrics."
)
