from __future__ import annotations

from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit


APP_VERSION = "2.4.3"

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
        "Intervals longer than this are treated as missing data. "
        "Use about 10 seconds for second-by-second exports, "
        "40 seconds for 30-second data and 70 seconds for one-minute data."
    ),
)

source = st.segmented_control(
    "Data source",
    ["Upload file", "Try reference brisket"],
    default="Upload file",
)


@st.cache_data
def load_workbook_bytes(data: bytes, filename: str):
    return engine.read_file(BytesIO(data), filename)


def elapsed_hours_by_phase(result) -> tuple[float, float]:
    """Return Cook and Hold hours from accepted intervals only.

    The previous interface could confuse total recorded duration with phase
    duration. This helper sums the actual elapsed seconds assigned to each
    phase after gap filtering.
    """
    timeline = result.timeline.copy()

    if timeline.empty:
        return 0.0, 0.0

    timeline = timeline[timeline["Status"] == "Analysed"].copy()
    elapsed = pd.to_numeric(timeline["Elapsed seconds"], errors="coerce").fillna(0.0)
    phase = timeline["Phase"].fillna("").astype(str)

    cook_seconds = float(elapsed[phase.str.contains("Cook", case=False, regex=False)].sum())
    hold_seconds = float(
        elapsed[
            phase.str.contains("Hold", case=False, regex=False)
            | phase.str.contains("cooldown", case=False, regex=False)
        ].sum()
    )

    # An interval crossing the detected pull point may contain both labels.
    # If the engine supplies a split interval as one row, avoid double-counting
    # by allocating the row according to the incremental rendering split is not
    # recoverable here. Current engine normally places the pull on an existing
    # sample, so this is a defensive fallback only.
    both = phase.str.contains("Cook", case=False, regex=False) & phase.str.contains(
        "Hold", case=False, regex=False
    )
    if both.any():
        overlap = float(elapsed[both].sum())
        cook_seconds -= overlap / 2.0
        hold_seconds -= overlap / 2.0

    return max(cook_seconds, 0.0) / 3600.0, max(hold_seconds, 0.0) / 3600.0


def make_multi_probe_frame(meat_results: dict) -> pd.DataFrame:
    """Build a tidy frame containing every valid meat-probe series."""
    frames = []

    for probe_name, result in meat_results.items():
        timeline = result.timeline.copy()
        if timeline.empty:
            continue

        required = {"Timestamp", "Temperature °C", "Status"}
        if not required.issubset(timeline.columns):
            continue

        timeline = timeline[timeline["Status"] == "Analysed"]
        timeline = timeline[["Timestamp", "Temperature °C"]].copy()
        timeline["Temperature °C"] = pd.to_numeric(
            timeline["Temperature °C"], errors="coerce"
        )
        timeline.dropna(subset=["Timestamp", "Temperature °C"], inplace=True)

        if timeline.empty:
            continue

        timeline["Probe"] = str(probe_name)
        frames.append(timeline)

    if not frames:
        return pd.DataFrame(columns=["Timestamp", "Temperature °C", "Probe"])

    comparison = pd.concat(frames, ignore_index=True)
    comparison.sort_values(["Timestamp", "Probe"], inplace=True)
    return comparison


def role_key(file_label: str, column_name: str) -> str:
    return f"{file_label}_{column_name}"


# ---------------------------------------------------------------------------
# Upload and column classification
# ---------------------------------------------------------------------------
if source == "Try reference brisket":
    primary_df = engine.demo_data()
    primary_timestamp = "timestamp"
    optional_df = None
    optional_timestamp = None

    classifications = pd.DataFrame(
        [
            {
                "Column": "Average Probe Temperature (°C)",
                "Suggested role": "Meat temperature",
                "Confidence": 99,
                "File": "Reference",
            }
        ]
    )
    effective_gap = max(float(max_gap), 70.0)
    st.info(
        "The reference profile uses one-minute readings. "
        "The effective maximum gap is therefore at least 70 seconds."
    )
else:
    primary_file = st.file_uploader(
        "Upload meat or combined temperature file",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="primary_file",
    )
    optional_file = st.file_uploader(
        "Optional second temperature file",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="optional_file",
    )

    if primary_file is None:
        st.info("Upload a meat or combined temperature file to begin.")
        st.stop()

    try:
        primary_sheets = load_workbook_bytes(
            primary_file.getvalue(), primary_file.name
        )
        primary_sheet = st.selectbox(
            "Primary worksheet", list(primary_sheets), key="primary_sheet"
        )
        primary_df = primary_sheets[primary_sheet]
        primary_timestamp, _ = engine.detect_columns(primary_df)

        classifications = pit.classify_columns(primary_df, primary_timestamp)
        classifications["File"] = "Primary"

        optional_df = None
        optional_timestamp = None

        if optional_file is not None:
            optional_sheets = load_workbook_bytes(
                optional_file.getvalue(), optional_file.name
            )
            optional_sheet = st.selectbox(
                "Optional worksheet", list(optional_sheets), key="optional_sheet"
            )
            optional_df = optional_sheets[optional_sheet]
            optional_timestamp, _ = engine.detect_columns(optional_df)

            optional_classifications = pit.classify_columns(
                optional_df, optional_timestamp
            )
            optional_classifications["File"] = "Optional"
            classifications = pd.concat(
                [classifications, optional_classifications], ignore_index=True
            )

        effective_gap = float(max_gap)

    except Exception as exc:
        st.error(f"File setup failed: {exc}")
        st.stop()

if classifications.empty:
    st.error("No usable temperature columns were found.")
    st.stop()

st.subheader("Review column classifications")
roles = {}

for _, row in classifications.iterrows():
    file_label = str(row["File"])
    column_name = str(row["Column"])
    suggested_role = str(row["Suggested role"])
    confidence = int(row["Confidence"])
    key = role_key(file_label, column_name)

    default_index = (
        pit.ROLES.index(suggested_role) if suggested_role in pit.ROLES else 0
    )

    roles[key] = st.selectbox(
        f"{file_label} • {column_name} ({confidence}% suggested confidence)",
        pit.ROLES,
        index=default_index,
        key=f"role_{key}",
    )

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
meat_results = {}
pit_results = {}
analysis_errors = []

for _, row in classifications.iterrows():
    file_label = str(row["File"])
    column_name = row["Column"]
    role = roles[role_key(file_label, str(column_name))]

    if role == "Ignore":
        continue

    if file_label in ("Primary", "Reference"):
        active_df = primary_df
        active_timestamp = primary_timestamp
    else:
        active_df = optional_df
        active_timestamp = optional_timestamp

    try:
        prepared_environment = pit.prepare(
            active_df, active_timestamp, column_name
        )

        if role in ("Meat temperature", "🥩 Point", "🥩 Flat", "🍖 Other Meat"):
            valid, report = engine.prepare(
                active_df, active_timestamp, column_name
            )
            detection = engine.classify_session(valid)
            result = engine.analyse(
                valid,
                report,
                detection,
                max_gap=effective_gap,
            )
            meat_results[f"{file_label}: {column_name}"] = result

        elif role in (
            "Grate temperature",
            "Controller temperature",
            "Target temperature",
            "🌡 Grate",
            "🔥 PID",
        ):
            normalised_role = {
                "🌡 Grate": "Grate temperature",
                "🔥 PID": "Controller temperature",
            }.get(role, role)
            pit_results[normalised_role] = pit.analyse(
                prepared_environment, normalised_role
            )

    except Exception as exc:
        analysis_errors.append(f"{file_label} • {column_name}: {exc}")

for message in analysis_errors:
    st.error(message)

if not meat_results:
    st.error("At least one column must be classified as a meat temperature.")
    st.stop()

# ---------------------------------------------------------------------------
# Multi-probe comparison: fixed by using tidy, concatenated probe data.
# ---------------------------------------------------------------------------
st.subheader("Multi-probe comparison")
comparison = make_multi_probe_frame(meat_results)

if comparison.empty:
    st.info("No valid probe readings were available for the comparison chart.")
else:
    comparison_chart = px.line(
        comparison,
        x="Timestamp",
        y="Temperature °C",
        color="Probe",
        labels={
            "Timestamp": "Time",
            "Temperature °C": "Internal temperature (°C)",
        },
    )
    comparison_chart.update_layout(
        height=480,
        legend_title_text="Probe",
    )
    st.plotly_chart(comparison_chart, use_container_width=True)

# ---------------------------------------------------------------------------
# Per-probe results and corrected phase durations.
# ---------------------------------------------------------------------------
st.subheader("Meat-probe analysis")

for name, result in meat_results.items():
    cook_hours, hold_hours = elapsed_hours_by_phase(result)

    with st.expander(name, expanded=len(meat_results) == 1):
        row_1 = st.columns(4)
        row_1[0].metric("Session", result.detection.session_type)
        row_1[1].metric("Cook duration", f"{cook_hours:.2f} h")
        row_1[2].metric("Hold duration", f"{hold_hours:.2f} h")
        row_1[3].metric(
            "Detected pull",
            result.detection.pull_timestamp.strftime("%d/%m/%Y %H:%M:%S")
            if result.detection.pull_timestamp is not None
            else "Not detected",
        )

        row_2 = st.columns(4)
        row_2[0].metric("Cook contribution", f"{result.cook:.1%}")
        row_2[1].metric("Hold contribution", f"{result.hold:.1%}")
        row_2[2].metric("Recorded total", f"{result.total:.1%}")
        row_2[3].metric(
            "Assessment",
            result.assessment if result.complete else "Partial session",
        )

        accepted = result.timeline[result.timeline["Status"] == "Analysed"]
        probe_chart = px.line(
            accepted,
            x="Timestamp",
            y="Temperature °C",
            color="Phase" if accepted["Phase"].nunique() > 1 else None,
            labels={"Timestamp": "Time"},
        )
        st.plotly_chart(probe_chart, use_container_width=True)

        with st.expander("Data quality and phase totals"):
            st.write(f"Analysed time above 60°C: {result.analysed_hours:.3f} h")
            st.write(f"Cook duration assigned: {cook_hours:.3f} h")
            st.write(f"Hold duration assigned: {hold_hours:.3f} h")
            st.write(f"Excluded gap time: {result.excluded_gap_hours:.3f} h")
            st.write(f"Time below 60°C: {result.below_model_hours:.3f} h")

# ---------------------------------------------------------------------------
# Pit/environment results.
# ---------------------------------------------------------------------------
if pit_results:
    st.subheader("Cooking-environment analysis")

    for name, result in pit_results.items():
        with st.expander(name, expanded=True):
            metrics = st.columns(4)
            metrics[0].metric("Average", f"{result.average:.1f}°C")
            metrics[1].metric("Minimum", f"{result.minimum:.1f}°C")
            metrics[2].metric("Maximum", f"{result.maximum:.1f}°C")
            metrics[3].metric("Stability", f"{result.stability_score:.0f}/100")

            environment_chart = px.line(
                result.timeline,
                x="timestamp",
                y="temperature_c",
                labels={
                    "timestamp": "Time",
                    "temperature_c": f"{name} (°C)",
                },
            )
            st.plotly_chart(environment_chart, use_container_width=True)

            if not result.lid_events.empty:
                st.write("Possible lid-open events")
                st.dataframe(
                    result.lid_events,
                    hide_index=True,
                    use_container_width=True,
                )

    first_meat = next(iter(meat_results.values()))
    overlay = pit.align(first_meat.timeline, pit_results)

    value_columns = [
        column for column in overlay.columns if column != "timestamp"
    ]
    overlay_long = overlay.melt(
        id_vars="timestamp",
        value_vars=value_columns,
        var_name="Temperature source",
        value_name="Temperature °C",
    ).dropna(subset=["Temperature °C"])

    st.subheader("Meat and cooking-environment overlay")
    if overlay_long.empty:
        st.info("No overlapping meat and environment readings were found.")
    else:
        overlay_chart = px.line(
            overlay_long,
            x="timestamp",
            y="Temperature °C",
            color="Temperature source",
        )
        st.plotly_chart(overlay_chart, use_container_width=True)

st.caption(
    "Pit stability and event detections are analytical estimates, "
    "not manufacturer-provided Weber metrics."
)
