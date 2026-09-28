from __future__ import annotations

from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit


APP_VERSION = "2.5.0"
MEAT_ROLES = {
    "Meat temperature",
    "🥩 Point",
    "🥩 Flat",
    "🍖 Other Meat",
}
ENVIRONMENT_ROLE_MAP = {
    "Grate temperature": "Grate temperature",
    "Controller temperature": "Controller temperature",
    "Target temperature": "Target temperature",
    "🌡 Grate": "Grate temperature",
    "🔥 PID": "Controller temperature",
}
ROLE_DISPLAY_NAMES = {
    "Meat temperature": "Meat",
    "🥩 Point": "🥩 Point",
    "🥩 Flat": "🥩 Flat",
    "🍖 Other Meat": "🍖 Other Meat",
}


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
        "Suggested values: 10 seconds for second-by-second exports, "
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


def role_key(file_label: str, column_name: str) -> str:
    return f"{file_label}_{column_name}"


def phase_hours(result) -> tuple[float, float]:
    """Read exact Cook and Hold durations from the engine's band summary.

    This is more accurate than deriving phase time from display labels because
    an interval that crosses the pull point is already divided correctly by
    the calculation engine before it reaches the summary table.
    """
    summary = result.summary.copy()

    if summary.empty or not {"Phase", "Duration hours"}.issubset(summary.columns):
        return 0.0, 0.0

    summary["Duration hours"] = pd.to_numeric(
        summary["Duration hours"], errors="coerce"
    ).fillna(0.0)

    cook_hours = float(
        summary.loc[summary["Phase"] == "Cook", "Duration hours"].sum()
    )
    hold_hours = float(
        summary.loc[
            summary["Phase"].isin(["Hold", "Hold / cooldown"]),
            "Duration hours",
        ].sum()
    )

    return cook_hours, hold_hours


def weighted_phase_temperature(result, phase_name: str) -> float | None:
    """Calculate a duration-weighted mean temperature for one phase."""
    timeline = result.timeline.copy()

    required = {"Temperature °C", "Elapsed seconds", "Phase", "Status"}
    if timeline.empty or not required.issubset(timeline.columns):
        return None

    timeline = timeline[timeline["Status"] == "Analysed"].copy()
    timeline["Temperature °C"] = pd.to_numeric(
        timeline["Temperature °C"], errors="coerce"
    )
    timeline["Elapsed seconds"] = pd.to_numeric(
        timeline["Elapsed seconds"], errors="coerce"
    )
    timeline.dropna(subset=["Temperature °C", "Elapsed seconds"], inplace=True)

    if phase_name == "Cook":
        mask = timeline["Phase"].fillna("").str.contains(
            "Cook", case=False, regex=False
        )
    else:
        phase_text = timeline["Phase"].fillna("")
        mask = phase_text.str.contains(
            "Hold", case=False, regex=False
        ) | phase_text.str.contains("cooldown", case=False, regex=False)

    selected = timeline.loc[mask]
    if selected.empty or selected["Elapsed seconds"].sum() <= 0:
        return None

    return float(
        (selected["Temperature °C"] * selected["Elapsed seconds"]).sum()
        / selected["Elapsed seconds"].sum()
    )


def make_probe_profile_name(
    selected_role: str,
    file_label: str,
    column_name: str,
    used_profile_names: set[str],
) -> str:
    """Create a concise but unique name for tables, charts and expanders."""
    role_name = ROLE_DISPLAY_NAMES.get(selected_role, selected_role)
    base_name = role_name

    if base_name in used_profile_names:
        base_name = f"{role_name} — {file_label} / {column_name}"

    used_profile_names.add(base_name)
    return base_name


def make_multi_probe_frame(meat_results: dict) -> pd.DataFrame:
    frames = []

    for profile_name, item in meat_results.items():
        result = item["result"]
        timeline = result.timeline.copy()

        required = {"Timestamp", "Temperature °C", "Status"}
        if timeline.empty or not required.issubset(timeline.columns):
            continue

        timeline = timeline[timeline["Status"] == "Analysed"]
        timeline = timeline[["Timestamp", "Temperature °C"]].copy()
        timeline["Temperature °C"] = pd.to_numeric(
            timeline["Temperature °C"], errors="coerce"
        )
        timeline.dropna(subset=["Timestamp", "Temperature °C"], inplace=True)

        if timeline.empty:
            continue

        timeline["Profile"] = profile_name
        frames.append(timeline)

    if not frames:
        return pd.DataFrame(columns=["Timestamp", "Temperature °C", "Profile"])

    comparison = pd.concat(frames, ignore_index=True)
    comparison.sort_values(["Timestamp", "Profile"], inplace=True)
    return comparison


# ---------------------------------------------------------------------------
# Step 1: Upload files
# ---------------------------------------------------------------------------
st.header("Step 1: Upload temperature files")

if source == "Try reference brisket":
    primary_df = engine.demo_data()
    primary_timestamp = "timestamp"
    primary_filename = "Built-in reference brisket"
    optional_df = None
    optional_timestamp = None
    optional_filename = None

    classifications = pd.DataFrame(
        [
            {
                "Column": "Average Probe Temperature (°C)",
                "Suggested role": (
                    "🍖 Other Meat"
                    if "🍖 Other Meat" in pit.ROLES
                    else "Meat temperature"
                ),
                "Confidence": 99,
                "File": "Primary",
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
        "Primary temperature file",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="primary_file",
    )
    optional_file = st.file_uploader(
        "Secondary temperature file (optional)",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="optional_file",
    )

    if primary_file is None:
        st.info("Upload a meat or combined temperature file to begin.")
        st.stop()

    try:
        primary_filename = primary_file.name
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
        optional_filename = None

        if optional_file is not None:
            optional_filename = optional_file.name
            optional_sheets = load_workbook_bytes(
                optional_file.getvalue(), optional_file.name
            )
            optional_sheet = st.selectbox(
                "Secondary worksheet", list(optional_sheets), key="secondary_sheet"
            )
            optional_df = optional_sheets[optional_sheet]
            optional_timestamp, _ = engine.detect_columns(optional_df)

            optional_classifications = pit.classify_columns(
                optional_df, optional_timestamp
            )
            optional_classifications["File"] = "Secondary"
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


# ---------------------------------------------------------------------------
# Step 2: Assign roles
# ---------------------------------------------------------------------------
st.header("Step 2: Assign probe roles")
st.caption(
    "Tell the analyser how each temperature channel was used. "
    "Point, Flat and Other Meat are analysed independently."
)

roles = {}
for file_label in classifications["File"].drop_duplicates().tolist():
    filename = (
        primary_filename if file_label == "Primary" else optional_filename
    )
    st.info(f"{file_label.upper()} FILE\n\n{filename}")

    file_rows = classifications[classifications["File"] == file_label]
    for _, row in file_rows.iterrows():
        column_name = str(row["Column"])
        suggested_role = str(row["Suggested role"])
        confidence = int(row["Confidence"])
        key = role_key(file_label, column_name)

        default_index = (
            pit.ROLES.index(suggested_role) if suggested_role in pit.ROLES else 0
        )

        roles[key] = st.selectbox(
            f"{column_name} ({confidence}% suggested confidence)",
            pit.ROLES,
            index=default_index,
            key=f"role_{key}",
        )

configuration_rows = []
for _, row in classifications.iterrows():
    file_label = str(row["File"])
    column_name = str(row["Column"])
    configuration_rows.append(
        {
            "Role": roles[role_key(file_label, column_name)],
            "Source": f"{file_label} / {column_name}",
        }
    )

st.subheader("Detected configuration")
st.dataframe(
    pd.DataFrame(configuration_rows), hide_index=True, use_container_width=True
)

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
meat_results = {}
pit_results = {}
analysis_errors = []
used_profile_names: set[str] = set()

for _, row in classifications.iterrows():
    file_label = str(row["File"])
    column_name = str(row["Column"])
    selected_role = roles[role_key(file_label, column_name)]

    if selected_role in {"Ignore", "🚫 Ignore"}:
        continue

    if file_label == "Primary":
        active_df = primary_df
        active_timestamp = primary_timestamp
    else:
        active_df = optional_df
        active_timestamp = optional_timestamp

    try:
        prepared_environment = pit.prepare(
            active_df, active_timestamp, column_name
        )

        if selected_role in MEAT_ROLES:
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

            profile_name = make_probe_profile_name(
                selected_role,
                file_label,
                column_name,
                used_profile_names,
            )
            meat_results[profile_name] = {
                "result": result,
                "source": f"{file_label} / {column_name}",
                "selected_role": selected_role,
            }

        elif selected_role in ENVIRONMENT_ROLE_MAP:
            normalised_role = ENVIRONMENT_ROLE_MAP[selected_role]
            environment_name = f"{selected_role} — {file_label} / {column_name}"
            pit_results[environment_name] = pit.analyse(
                prepared_environment, normalised_role
            )

    except Exception as exc:
        analysis_errors.append(f"{file_label} • {column_name}: {exc}")

for message in analysis_errors:
    st.error(message)

if not meat_results:
    st.error("At least one column must be classified as a meat probe.")
    st.stop()


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------
summary_rows = []
for profile_name, item in meat_results.items():
    result = item["result"]
    cook_hours, hold_hours = phase_hours(result)
    average_cook = weighted_phase_temperature(result, "Cook")
    average_hold = weighted_phase_temperature(result, "Hold")

    summary_rows.append(
        {
            "Profile": profile_name,
            "Source": item["source"],
            "Session type": result.detection.session_type,
            "Confidence": f"{result.detection.confidence}%",
            "Detected pull": result.detection.pull_timestamp,
            "Peak °C": round(result.detection.peak_temperature, 1),
            "Average °C": round(result.detection.average_temperature, 1),
            "Minimum °C": round(result.detection.minimum_temperature, 1),
            "Cook hours": round(cook_hours, 2),
            "Hold hours": round(hold_hours, 2),
            "Average cook °C": (
                round(average_cook, 1) if average_cook is not None else None
            ),
            "Average hold °C": (
                round(average_hold, 1) if average_hold is not None else None
            ),
        }
    )

st.subheader("Session detection summary")
st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Multi-probe comparison
# ---------------------------------------------------------------------------
st.header("Multi-probe comparison")
comparison_summary = []

for profile_name, item in meat_results.items():
    result = item["result"]
    comparison_summary.append(
        {
            "Profile": profile_name,
            "Source": item["source"],
            "Cook contribution": result.cook,
            "Hold contribution": result.hold,
            "Recorded total": result.total,
            "Assessment": result.assessment,
            "Analysed hours": result.analysed_hours,
        }
    )

comparison_table = pd.DataFrame(comparison_summary)
st.dataframe(
    comparison_table,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Cook contribution": st.column_config.NumberColumn(format="%.1f%%"),
        "Hold contribution": st.column_config.NumberColumn(format="%.1f%%"),
        "Recorded total": st.column_config.NumberColumn(format="%.1f%%"),
        "Analysed hours": st.column_config.NumberColumn(format="%.2f"),
    },
)

comparison = make_multi_probe_frame(meat_results)
st.subheader("All classified meat probes")

if comparison.empty:
    st.info("No valid probe readings were available for the comparison chart.")
else:
    comparison_chart = px.line(
        comparison,
        x="Timestamp",
        y="Temperature °C",
        color="Profile",
        labels={
            "Timestamp": "Timestamp",
            "Temperature °C": "Temperature °C",
        },
    )
    comparison_chart.update_layout(height=480, legend_title_text="Profile")
    st.plotly_chart(comparison_chart, use_container_width=True)


# ---------------------------------------------------------------------------
# Per-probe details
# ---------------------------------------------------------------------------
st.header("Meat-probe analysis")

for profile_name, item in meat_results.items():
    result = item["result"]
    cook_hours, hold_hours = phase_hours(result)

    with st.expander(
        f"{profile_name} — {item['source']}",
        expanded=len(meat_results) == 1,
    ):
        metrics_1 = st.columns(4)
        metrics_1[0].metric("Session", result.detection.session_type)
        metrics_1[1].metric("Cook duration", f"{cook_hours:.2f} h")
        metrics_1[2].metric("Hold duration", f"{hold_hours:.2f} h")
        metrics_1[3].metric(
            "Detected pull",
            result.detection.pull_timestamp.strftime("%d/%m/%Y %H:%M:%S")
            if result.detection.pull_timestamp is not None
            else "Not detected",
        )

        metrics_2 = st.columns(4)
        metrics_2[0].metric("Cook contribution", f"{result.cook:.1%}")
        metrics_2[1].metric("Hold contribution", f"{result.hold:.1%}")
        metrics_2[2].metric("Recorded total", f"{result.total:.1%}")
        metrics_2[3].metric(
            "Assessment",
            result.assessment if result.complete else "Partial session",
        )

        accepted = result.timeline[result.timeline["Status"] == "Analysed"]
        probe_chart = px.line(
            accepted,
            x="Timestamp",
            y="Temperature °C",
            color="Phase" if accepted["Phase"].nunique() > 1 else None,
        )
        st.plotly_chart(probe_chart, use_container_width=True)

        with st.expander("Data quality and calculations"):
            st.write(f"Analysed time above 60°C: {result.analysed_hours:.3f} h")
            st.write(f"Cook duration assigned: {cook_hours:.3f} h")
            st.write(f"Hold duration assigned: {hold_hours:.3f} h")
            st.write(f"Excluded gap time: {result.excluded_gap_hours:.3f} h")
            st.write(f"Time below 60°C: {result.below_model_hours:.3f} h")


# ---------------------------------------------------------------------------
# Cooking-environment details
# ---------------------------------------------------------------------------
if pit_results:
    st.header("Cooking-environment analysis")

    for environment_name, result in pit_results.items():
        with st.expander(environment_name, expanded=True):
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
                    "timestamp": "timestamp",
                    "temperature_c": "Temperature °C",
                },
                title=environment_name,
            )
            st.plotly_chart(environment_chart, use_container_width=True)

            if not result.lid_events.empty:
                st.write("Possible lid-open events")
                st.dataframe(
                    result.lid_events,
                    hide_index=True,
                    use_container_width=True,
                )

    first_meat_result = next(iter(meat_results.values()))["result"]
    overlay = pit.align(first_meat_result.timeline, pit_results)
    value_columns = [column for column in overlay.columns if column != "timestamp"]
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
