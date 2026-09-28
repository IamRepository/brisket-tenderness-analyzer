from datetime import datetime
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit


APP_VERSION = "2.4.3"
MEAT_ROLES = ("🥩 Point", "🥩 Flat", "🍖 Other Meat")
ENVIRONMENT_ROLES = ("🌡 Grate", "🔥 PID")
IGNORE_ROLE = "🚫 Ignore"


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
    max_value=7200.0,
    value=10.0,
    step=1.0,
    help=(
        "Intervals longer than this are excluded. The analyser also protects "
        "regularly sampled files by adapting to the file's normal interval."
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


def unique_name(role: str, source_name: str, existing: dict) -> str:
    if role not in existing:
        return role
    candidate = f"{role} — {source_name}"
    if candidate not in existing:
        return candidate
    counter = 2
    while f"{candidate} ({counter})" in existing:
        counter += 1
    return f"{candidate} ({counter})"


def adaptive_gap_seconds(valid: pd.DataFrame, requested_gap: float) -> float:
    """Allow regular 15-second, 30-second, 1-minute or 15-minute test files."""
    intervals = (
        valid["timestamp"]
        .sort_values()
        .diff()
        .dt.total_seconds()
        .dropna()
    )
    intervals = intervals[intervals > 0]
    if intervals.empty:
        return float(requested_gap)
    normal_interval = float(intervals.median())
    return max(float(requested_gap), normal_interval * 1.25)


def prepare_phase_statistics(valid: pd.DataFrame, detection) -> dict:
    """Calculate phase durations and weighted temperatures directly from valid data."""
    work = valid.copy().sort_values("timestamp").reset_index(drop=True)
    work["next_timestamp"] = work["timestamp"].shift(-1)
    work["elapsed_seconds"] = (
        work["next_timestamp"] - work["timestamp"]
    ).dt.total_seconds()
    work = work[work["elapsed_seconds"].notna() & (work["elapsed_seconds"] > 0)]

    if work.empty:
        return {
            "cook_hours": 0.0,
            "hold_hours": 0.0,
            "average_cook": None,
            "average_hold": None,
        }

    pull = detection.pull_timestamp
    if detection.session_type == "Cook Only":
        cook = work
        hold = work.iloc[0:0]
    elif detection.session_type in ("Hold Only", "Calibration / Hold Test"):
        cook = work.iloc[0:0]
        hold = work
    elif pull is not None:
        pull = pd.Timestamp(pull)
        cook = work[work["timestamp"] < pull]
        hold = work[work["timestamp"] >= pull]
    else:
        cook = work.iloc[0:0]
        hold = work.iloc[0:0]

    def weighted_average(frame):
        if frame.empty:
            return None
        weights = frame["elapsed_seconds"].clip(lower=0)
        if float(weights.sum()) == 0:
            return float(frame["temperature_c"].mean())
        return float((frame["temperature_c"] * weights).sum() / weights.sum())

    return {
        "cook_hours": float(cook["elapsed_seconds"].sum()) / 3600.0,
        "hold_hours": float(hold["elapsed_seconds"].sum()) / 3600.0,
        "average_cook": weighted_average(cook),
        "average_hold": weighted_average(hold),
    }


def metric_temperature(value):
    return "N/A" if value is None else f"{value:.1f}°C"


# -----------------------------------------------------------------------------
# Step 1: Upload source files
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
    requested_gap = max(float(max_gap), 70.0)
else:
    st.subheader("Step 1: Upload temperature files")
    primary = st.file_uploader(
        "Primary temperature file",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="primary_file",
    )
    optional = st.file_uploader(
        "Secondary temperature file (optional)",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="secondary_file",
    )

    if primary is None:
        st.info("Upload a primary temperature file to begin.")
        st.stop()

    primary_name = primary.name
    primary_sheets = load_file(primary.getvalue(), primary.name)
    primary_sheet = st.selectbox(
        "Primary worksheet",
        list(primary_sheets),
        key="primary_sheet",
    )
    df = primary_sheets[primary_sheet]
    timestamp_col, _ = engine.detect_columns(df)

    classifications = pit.classify_columns(df, timestamp_col)
    classifications["File"] = "Primary"

    extra_df = None
    extra_timestamp = None
    optional_name = None

    if optional is not None:
        optional_name = optional.name
        secondary_sheets = load_file(optional.getvalue(), optional.name)
        secondary_sheet = st.selectbox(
            "Secondary worksheet",
            list(secondary_sheets),
            key="secondary_sheet",
        )
        extra_df = secondary_sheets[secondary_sheet]
        extra_timestamp, _ = engine.detect_columns(extra_df)
        secondary_classifications = pit.classify_columns(
            extra_df,
            extra_timestamp,
        )
        secondary_classifications["File"] = "Secondary"
        classifications = pd.concat(
            [classifications, secondary_classifications],
            ignore_index=True,
        )

    requested_gap = float(max_gap)


# -----------------------------------------------------------------------------
# Step 2: Assign roles
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

    displayed_name = (
        primary_name
        if file_role in ("Primary", "Reference")
        else optional_name
    )
    st.markdown(
        f"""
        <div style="background:{colour}; padding:12px 16px; border-radius:10px;
                    margin:14px 0 8px 0; border:1px solid #d8dee6;">
          <strong>{icon} {file_role.upper()} FILE</strong><br>
          <span style="font-size:0.9rem; color:#4d5966;">{displayed_name or file_role}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for _, row in group.iterrows():
        role_key = f"{file_role}_{row['Column']}"
        suggested = row["Suggested role"]
        suggested_index = (
            pit.ROLES.index(suggested)
            if suggested in pit.ROLES
            else pit.ROLES.index(IGNORE_ROLE)
        )
        roles[role_key] = st.selectbox(
            f"{row['Column']} ({row['Confidence']}% suggested confidence)",
            pit.ROLES,
            index=suggested_index,
            key=f"role_{role_key}",
        )

st.markdown("#### Detected configuration")
configuration_rows = []
for _, row in classifications.iterrows():
    file_role = row["File"]
    role_key = f"{file_role}_{row['Column']}"
    selected_role = roles[role_key]
    if selected_role != IGNORE_ROLE:
        configuration_rows.append(
            {
                "Role": selected_role,
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
    st.warning("All detected temperature columns are set to Ignore.")

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()


# -----------------------------------------------------------------------------
# Step 3: Analyse all selected profiles
# -----------------------------------------------------------------------------
meat_results = {}
meat_valid_data = {}
meat_sources = {}
pit_results = {}

for _, row in classifications.iterrows():
    file_role = row["File"]
    column = row["Column"]
    role_key = f"{file_role}_{column}"
    selected_role = roles[role_key]

    if selected_role == IGNORE_ROLE:
        continue

    active_df = df if file_role in ("Primary", "Reference") else extra_df
    active_timestamp = (
        timestamp_col
        if file_role in ("Primary", "Reference")
        else extra_timestamp
    )
    source_name = source_label(file_role, str(column))

    if selected_role in MEAT_ROLES:
        valid, report = engine.prepare(active_df, active_timestamp, column)
        detection = engine.classify_session(valid)
        effective_gap = adaptive_gap_seconds(valid, requested_gap)
        profile_name = unique_name(selected_role, source_name, meat_results)

        # Pass max_gap by keyword. This avoids accidentally filling pull_override.
        result = engine.analyse(
            valid,
            report,
            detection,
            max_gap=effective_gap,
        )
        meat_results[profile_name] = result
        meat_valid_data[profile_name] = valid
        meat_sources[profile_name] = source_name

    elif selected_role in ENVIRONMENT_ROLES:
        prepared = pit.prepare(active_df, active_timestamp, column)
        environment_name = f"{selected_role} — {source_name}"
        pit_results[environment_name] = pit.analyse(
            prepared,
            selected_role,
        )

if not meat_results:
    st.error(
        "At least one column must be classified as Point, Flat or Other Meat."
    )
    st.stop()


# -----------------------------------------------------------------------------
# Session detection summary
# -----------------------------------------------------------------------------
st.subheader("Session detection summary")
summary_rows = []
phase_stats_by_profile = {}

for profile_name, result in meat_results.items():
    detection = result.detection
    phase_stats = prepare_phase_statistics(
        meat_valid_data[profile_name],
        detection,
    )
    phase_stats_by_profile[profile_name] = phase_stats

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
# Multi-probe comparison
# -----------------------------------------------------------------------------
st.subheader("Multi-probe comparison")
comparison_rows = []
comparison_frames = []

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

    # Build the comparison chart from the validated probe data, not filtered
    # timeline rows. This guarantees that every selected meat probe is plotted.
    profile_frame = meat_valid_data[profile_name][
        ["timestamp", "temperature_c"]
    ].copy()
    profile_frame.columns = ["Timestamp", "Temperature °C"]
    profile_frame["Profile"] = profile_name
    comparison_frames.append(profile_frame)

comparison_df = pd.DataFrame(comparison_rows)
comparison_display = comparison_df.copy()
for percentage_column in (
    "Cook contribution",
    "Hold contribution",
    "Recorded total",
):
    comparison_display[percentage_column] = comparison_display[percentage_column].map(
        lambda value: f"{value:.1%}"
    )
comparison_display["Analysed hours"] = comparison_display["Analysed hours"].round(2)

st.dataframe(
    comparison_display,
    hide_index=True,
    use_container_width=True,
)

if comparison_frames:
    combined_meat = pd.concat(comparison_frames, ignore_index=True)
    probe_chart = px.line(
        combined_meat,
        x="Timestamp",
        y="Temperature °C",
        color="Profile",
        title="All classified meat probes",
    )
    st.plotly_chart(
        probe_chart,
        use_container_width=True,
        key="all_classified_meat_probes_chart",
    )
else:
    st.info("No valid probe readings were available for the comparison chart.")


# -----------------------------------------------------------------------------
# Detailed analysis for each meat probe
# -----------------------------------------------------------------------------
st.subheader("Meat-probe analysis")

for profile_index, (profile_name, result) in enumerate(meat_results.items()):
    detection = result.detection
    phase_stats = phase_stats_by_profile[profile_name]
    safe_key = f"meat_{profile_index}"

    with st.expander(
        f"{profile_name} — {meat_sources[profile_name]}",
        expanded=len(meat_results) == 1,
    ):
        row1a, row1b, row1c, row1d = st.columns(4)
        row1a.metric("Session", detection.session_type)
        row1b.metric("Confidence", f"{detection.confidence}%")
        row1c.metric(
            "Detected pull",
            detection.pull_timestamp.strftime("%d/%m/%Y %H:%M")
            if detection.pull_timestamp is not None
            else "Not detected",
        )
        row1d.metric("Peak temperature", f"{detection.peak_temperature:.1f}°C")

        row2a, row2b, row2c, row2d = st.columns(4)
        row2a.metric("Cook contribution", f"{result.cook:.1%}")
        row2b.metric("Hold contribution", f"{result.hold:.1%}")
        row2c.metric("Recorded total", f"{result.total:.1%}")
        row2d.metric(
            "Assessment",
            result.assessment if result.complete else "Partial session",
        )

        row3a, row3b, row3c, row3d = st.columns(4)
        row3a.metric("Cook duration", f"{phase_stats['cook_hours']:.2f} h")
        row3b.metric("Hold duration", f"{phase_stats['hold_hours']:.2f} h")
        row3c.metric(
            "Average cook temperature",
            metric_temperature(phase_stats["average_cook"]),
        )
        row3d.metric(
            "Average hold temperature",
            metric_temperature(phase_stats["average_hold"]),
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
            temperature_frame = meat_valid_data[profile_name][
                ["timestamp", "temperature_c"]
            ].copy()
            temperature_frame.columns = ["Timestamp", "Temperature °C"]
            temperature_chart = px.line(
                temperature_frame,
                x="Timestamp",
                y="Temperature °C",
                title=profile_name,
            )
            st.plotly_chart(
                temperature_chart,
                use_container_width=True,
                key=f"{safe_key}_temperature_chart",
            )

        with tabs[1]:
            rendering_chart = px.line(
                result.timeline,
                x="Timestamp",
                y="Accumulated rendering",
                title=f"Accumulated rendering — {profile_name}",
            )
            rendering_chart.update_yaxes(tickformat=".0%")
            st.plotly_chart(
                rendering_chart,
                use_container_width=True,
                key=f"{safe_key}_rendering_chart",
            )

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
                key=f"{safe_key}_band_table",
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
# Environmental analysis and all-source overlay
# -----------------------------------------------------------------------------
if pit_results:
    st.subheader("Cooking-environment analysis")

    for environment_index, (environment_name, result) in enumerate(
        pit_results.items()
    ):
        safe_key = f"environment_{environment_index}"
        with st.expander(environment_name, expanded=True):
            env1, env2, env3, env4 = st.columns(4)
            env1.metric("Average", f"{result.average:.1f}°C")
            env2.metric("Minimum", f"{result.minimum:.1f}°C")
            env3.metric("Maximum", f"{result.maximum:.1f}°C")
            env4.metric("Stability", f"{result.stability_score:.0f}/100")

            environment_chart = px.line(
                result.timeline,
                x="timestamp",
                y="temperature_c",
                title=environment_name,
                labels={"temperature_c": "Temperature °C"},
            )
            st.plotly_chart(
                environment_chart,
                use_container_width=True,
                key=f"{safe_key}_chart",
            )

            if len(result.lid_events):
                st.write("Possible lid-open events")
                st.dataframe(
                    result.lid_events,
                    hide_index=True,
                    use_container_width=True,
                    key=f"{safe_key}_lid_events",
                )

    overlay_frames = []

    for profile_name, valid in meat_valid_data.items():
        frame = valid[["timestamp", "temperature_c"]].copy()
        frame.columns = ["timestamp", "Temperature °C"]
        frame["Temperature source"] = profile_name
        overlay_frames.append(frame)

    for environment_name, result in pit_results.items():
        frame = result.timeline[["timestamp", "temperature_c"]].copy()
        frame.columns = ["timestamp", "Temperature °C"]
        frame["Temperature source"] = environment_name
        overlay_frames.append(frame)

    all_sources = pd.concat(overlay_frames, ignore_index=True)
    all_sources.sort_values("timestamp", inplace=True)

    overlay_chart = px.line(
        all_sources,
        x="timestamp",
        y="Temperature °C",
        color="Temperature source",
        title="Meat and cooking-environment overlay",
    )
    st.plotly_chart(
        overlay_chart,
        use_container_width=True,
        key="meat_and_environment_overlay_chart",
    )


st.caption(
    "Pit stability and event detections are analytical estimates, "
    "not manufacturer-provided Weber metrics."
)
