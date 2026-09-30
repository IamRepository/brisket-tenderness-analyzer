from __future__ import annotations

from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit


APP_VERSION = "2.6.0"
MEAT_ROLES = ("🥩 Point", "🥩 Flat", "🍖 Other Meat")
COOK_ENVIRONMENT_ROLES = ("🔥 Cook PID", "🌡 Cook Grate")
HOLD_ENVIRONMENT_ROLES = ("🌡 Hold Environment",)
ENVIRONMENT_ROLES = COOK_ENVIRONMENT_ROLES + HOLD_ENVIRONMENT_ROLES
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

with st.sidebar:
    st.header("Settings")
    with st.expander("Advanced sampling settings"):
        manual_override = st.checkbox(
            "Override automatic gap detection",
            value=False,
            help=(
                "Leave this off for normal use. The analyser detects the normal "
                "sampling interval for each meat probe automatically."
            ),
        )
        manual_gap = None
        if manual_override:
            manual_gap = st.number_input(
                "Maximum accepted gap (seconds)",
                min_value=1.0,
                max_value=86400.0,
                value=10.0,
                step=1.0,
            )

source = st.segmented_control(
    "Data source",
    ["Upload file", "Try reference brisket"],
    default="Upload file",
)


@st.cache_data
def load_file(data: bytes, name: str):
    return engine.read_file(BytesIO(data), name)


def format_interval(seconds):
    if seconds is None:
        return "Unknown"
    if seconds < 60:
        return f"{seconds:.0f} sec"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"


def sampling_settings(valid: pd.DataFrame, override=None) -> dict:
    intervals = valid["timestamp"].sort_values().diff().dt.total_seconds().dropna()
    intervals = intervals[intervals > 0]
    detected = float(intervals.median()) if len(intervals) else None
    automatic = max(detected * 3.0, detected + 1.0) if detected is not None else 10.0
    selected = float(override) if override is not None else automatic
    return {
        "interval": detected,
        "automatic": automatic,
        "selected": selected,
        "manual": override is not None,
    }


def source_label(file_role, column):
    return f"{file_role} / {column}"


def clean_role(role):
    return {
        "🥩 Point": "Point",
        "🥩 Flat": "Flat",
        "🍖 Other Meat": "Other Meat",
        "🔥 Cook PID": "Cook PID",
        "🌡 Cook Grate": "Cook Grate",
        "🌡 Hold Environment": "Hold Environment",
    }.get(role, role)


def unique_label(role, file_role, column, existing):
    base = f"{clean_role(role)} — {column}"
    if base not in existing:
        return base
    candidate = f"{clean_role(role)} — {file_role} / {column}"
    number = 2
    while candidate in existing:
        candidate = f"{clean_role(role)} — {file_role} / {column} ({number})"
        number += 1
    return candidate


def phase_stats(valid, detection):
    work = valid.sort_values("timestamp").copy()
    work["elapsed"] = (work["timestamp"].shift(-1) - work["timestamp"]).dt.total_seconds()
    work = work[work["elapsed"].notna() & (work["elapsed"] > 0)]
    pull = detection.pull_timestamp

    if detection.session_type == "Cook Only":
        cook, hold = work, work.iloc[0:0]
    elif detection.session_type in ("Hold Only", "Calibration / Hold Test"):
        cook, hold = work.iloc[0:0], work
    elif pull is not None:
        pull = pd.Timestamp(pull)
        cook = work[work["timestamp"] < pull]
        hold = work[work["timestamp"] >= pull]
    else:
        cook, hold = work.iloc[0:0], work.iloc[0:0]

    def weighted_temperature(frame):
        if frame.empty or frame["elapsed"].sum() <= 0:
            return None
        return float((frame["temperature_c"] * frame["elapsed"]).sum() / frame["elapsed"].sum())

    return {
        "cook_hours": float(cook["elapsed"].sum()) / 3600.0,
        "hold_hours": float(hold["elapsed"].sum()) / 3600.0,
        "cook_temperature": weighted_temperature(cook),
        "hold_temperature": weighted_temperature(hold),
    }


def trim_environment_to_phase(prepared, role, transfer_time):
    """Assign environment data to Cook or Hold without mixing environments."""
    if transfer_time is None:
        return prepared.copy()
    transfer_time = pd.Timestamp(transfer_time)
    if role in COOK_ENVIRONMENT_ROLES:
        return prepared[prepared["timestamp"] <= transfer_time].copy()
    if role in HOLD_ENVIRONMENT_ROLES:
        return prepared[prepared["timestamp"] >= transfer_time].copy()
    return prepared.copy()


def environment_statistics(frame):
    if frame.empty:
        return {"duration": 0.0, "average": None, "minimum": None, "maximum": None}
    values = frame["temperature_c"].astype(float)
    duration = (frame["timestamp"].max() - frame["timestamp"].min()).total_seconds() / 3600.0
    return {
        "duration": max(duration, 0.0),
        "average": float(values.mean()),
        "minimum": float(values.min()),
        "maximum": float(values.max()),
    }


def derive_environment_curve(environment_profiles, preferred_roles):
    """Create one derived curve using role priority, never averaging PID and Grate."""
    for preferred_role in preferred_roles:
        candidates = [
            item for item in environment_profiles.values()
            if item["role"] == preferred_role and not item["data"].empty
        ]
        if candidates:
            selected = candidates[0]
            curve = selected["data"][["timestamp", "temperature_c"]].copy()
            curve["Source role"] = preferred_role
            return curve
    return pd.DataFrame(columns=["timestamp", "temperature_c", "Source role"])


# Step 1: Upload
st.header("Step 1: Upload temperature files")
if source == "Try reference brisket":
    primary_name = "Reference brisket"
    primary_df = engine.demo_data()
    primary_timestamp = "timestamp"
    secondary_name = None
    secondary_df = None
    secondary_timestamp = None
    classifications = pd.DataFrame([{
        "Column": "Average Probe Temperature (°C)",
        "Suggested role": "🍖 Other Meat",
        "Confidence": 99,
        "File": "Primary",
    }])
else:
    primary_file = st.file_uploader(
        "Primary temperature file",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="primary_file",
    )
    secondary_file = st.file_uploader(
        "Secondary temperature file (optional)",
        type=["xlsx", "xlsm", "xls", "csv"],
        key="secondary_file",
    )
    if primary_file is None:
        st.info("Upload a primary temperature file to begin.")
        st.stop()

    primary_name = primary_file.name
    sheets = load_file(primary_file.getvalue(), primary_file.name)
    sheet = st.selectbox("Primary worksheet", list(sheets), key="primary_sheet")
    primary_df = sheets[sheet]
    primary_timestamp, _ = engine.detect_columns(primary_df)
    classifications = pit.classify_columns(primary_df, primary_timestamp)
    classifications["File"] = "Primary"

    secondary_name = None
    secondary_df = None
    secondary_timestamp = None
    if secondary_file is not None:
        secondary_name = secondary_file.name
        secondary_sheets = load_file(secondary_file.getvalue(), secondary_file.name)
        secondary_sheet = st.selectbox(
            "Secondary worksheet", list(secondary_sheets), key="secondary_sheet"
        )
        secondary_df = secondary_sheets[secondary_sheet]
        secondary_timestamp, _ = engine.detect_columns(secondary_df)
        secondary_classification = pit.classify_columns(
            secondary_df, secondary_timestamp
        )
        secondary_classification["File"] = "Secondary"
        classifications = pd.concat(
            [classifications, secondary_classification], ignore_index=True
        )

# Step 2: Roles
st.header("Step 2: Assign probe and environment roles")
st.caption(
    "Meat probes continue through Cook and Hold. Cook PID and Cook Grate belong "
    "to the smoker. Hold Environment belongs to the oven or holding equipment."
)
roles = {}
for file_role, colour, icon in (
    ("Primary", "#eaf3ff", "🟦"),
    ("Secondary", "#edf9ef", "🟩"),
):
    group = classifications[classifications["File"] == file_role]
    if group.empty:
        continue
    filename = primary_name if file_role == "Primary" else secondary_name
    st.markdown(
        f'<div style="background:{colour};padding:12px 16px;border-radius:10px;'
        f'border:1px solid #d8dee6;margin-top:12px"><b>{icon} {file_role.upper()} FILE</b>'
        f'<br><small>{filename}</small></div>',
        unsafe_allow_html=True,
    )
    for _, row in group.iterrows():
        key = f"{file_role}_{row['Column']}"
        suggested = row["Suggested role"]
        default_index = pit.ROLES.index(suggested) if suggested in pit.ROLES else pit.ROLES.index(IGNORE_ROLE)
        roles[key] = st.selectbox(
            f"{row['Column']} ({row['Confidence']}% suggested confidence)",
            pit.ROLES,
            index=default_index,
            key=f"role_{key}",
        )

configuration = []
for _, row in classifications.iterrows():
    key = f"{row['File']}_{row['Column']}"
    if roles[key] != IGNORE_ROLE:
        configuration.append({"Role": roles[key], "Source": source_label(row["File"], row["Column"])})
st.subheader("Detected configuration")
st.dataframe(pd.DataFrame(configuration), hide_index=True, use_container_width=True)

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()

# Analyse meat first to establish transfer candidates
meat_results = {}
meat_data = {}
meat_sources = {}
sampling_info = {}
used_meat_labels = {}
environment_inputs = []

for _, row in classifications.iterrows():
    file_role, column = row["File"], row["Column"]
    selected_role = roles[f"{file_role}_{column}"]
    if selected_role == IGNORE_ROLE:
        continue
    active_df = primary_df if file_role == "Primary" else secondary_df
    active_timestamp = primary_timestamp if file_role == "Primary" else secondary_timestamp

    if selected_role in MEAT_ROLES:
        valid, report = engine.prepare(active_df, active_timestamp, column)
        detection = engine.classify_session(valid)
        sampling = sampling_settings(valid, manual_gap)
        label = unique_label(selected_role, file_role, str(column), used_meat_labels)
        result = engine.analyse(valid, report, detection, max_gap=sampling["selected"])
        meat_results[label] = result
        meat_data[label] = valid
        meat_sources[label] = source_label(file_role, column)
        sampling_info[label] = sampling
        used_meat_labels[label] = True
    elif selected_role in ENVIRONMENT_ROLES:
        prepared = pit.prepare(active_df, active_timestamp, column)
        environment_inputs.append({
            "role": selected_role,
            "source": source_label(file_role, column),
            "data": prepared,
        })

if not meat_results:
    st.error("At least one column must be classified as Point, Flat or Other Meat.")
    st.stop()

# Transfer time: use latest detected meat pull. User can override.
pull_candidates = [
    result.detection.pull_timestamp for result in meat_results.values()
    if result.detection.pull_timestamp is not None
]
auto_transfer = max(pull_candidates) if pull_candidates else None

st.header("Environment transition")
if auto_transfer is not None:
    st.info(f"Suggested smoker-to-hold transfer: {auto_transfer:%d/%m/%Y %H:%M}")
    override_transfer = st.checkbox("Override transfer timestamp", value=False)
    if override_transfer:
        col_date, col_time = st.columns(2)
        transfer_date = col_date.date_input("Transfer date", auto_transfer.date())
        transfer_time_value = col_time.time_input("Transfer time", auto_transfer.time(), step=60)
        transfer_time = pd.Timestamp.combine(transfer_date, transfer_time_value)
    else:
        transfer_time = pd.Timestamp(auto_transfer)
else:
    st.warning("No automatic transfer time was detected. Environment data will not be phase-trimmed.")
    transfer_time = None

# Environment phase allocation
environment_profiles = {}
for item in environment_inputs:
    trimmed = trim_environment_to_phase(item["data"], item["role"], transfer_time)
    label = f"{clean_role(item['role'])} — {item['source']}"
    environment_profiles[label] = {
        "role": item["role"],
        "source": item["source"],
        "data": trimmed,
        "stats": environment_statistics(trimmed),
    }

cook_environment = derive_environment_curve(
    environment_profiles,
    preferred_roles=("🌡 Cook Grate", "🔥 Cook PID"),
)
hold_environment = derive_environment_curve(
    environment_profiles,
    preferred_roles=("🌡 Hold Environment",),
)

# Session summary
st.header("Session detection summary")
summary_rows = []
phase_stats_by_profile = {}
for label, result in meat_results.items():
    stats = phase_stats(meat_data[label], result.detection)
    phase_stats_by_profile[label] = stats
    sampling = sampling_info[label]
    summary_rows.append({
        "Profile": label,
        "Source": meat_sources[label],
        "Session type": result.detection.session_type,
        "Confidence": f"{result.detection.confidence}%",
        "Detected pull": result.detection.pull_timestamp,
        "Peak °C": round(result.detection.peak_temperature, 1),
        "Cook hours": round(stats["cook_hours"], 2),
        "Hold hours": round(stats["hold_hours"], 2),
        "Average cook °C": None if stats["cook_temperature"] is None else round(stats["cook_temperature"], 1),
        "Average hold °C": None if stats["hold_temperature"] is None else round(stats["hold_temperature"], 1),
        "Sampling": format_interval(sampling["interval"]),
        "Gap threshold": format_interval(sampling["selected"]),
    })
st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

# Meat comparison
st.header("Multi-probe comparison")
comparison_rows, comparison_frames = [], []
for label, result in meat_results.items():
    comparison_rows.append({
        "Profile": label,
        "Source": meat_sources[label],
        "Cook contribution": f"{result.cook:.1%}",
        "Hold contribution": f"{result.hold:.1%}",
        "Recorded total": f"{result.total:.1%}",
        "Assessment": result.assessment if result.complete else "Partial session",
        "Analysed hours": round(result.analysed_hours, 2),
    })
    frame = meat_data[label][["timestamp", "temperature_c"]].copy()
    frame.columns = ["Timestamp", "Temperature °C"]
    frame["Profile"] = label
    comparison_frames.append(frame)
st.dataframe(pd.DataFrame(comparison_rows), hide_index=True, use_container_width=True)
comparison = pd.concat(comparison_frames, ignore_index=True)
st.plotly_chart(
    px.line(comparison, x="Timestamp", y="Temperature °C", color="Profile", title="All classified meat probes"),
    use_container_width=True,
    key="meat_comparison",
)

# Environment audit view
st.header("Environment analysis")
environment_rows = []
for label, item in environment_profiles.items():
    stats = item["stats"]
    environment_rows.append({
        "Profile": label,
        "Role": item["role"],
        "Source": item["source"],
        "Duration h": round(stats["duration"], 2),
        "Average °C": None if stats["average"] is None else round(stats["average"], 1),
        "Minimum °C": None if stats["minimum"] is None else round(stats["minimum"], 1),
        "Maximum °C": None if stats["maximum"] is None else round(stats["maximum"], 1),
    })
if environment_rows:
    st.dataframe(pd.DataFrame(environment_rows), hide_index=True, use_container_width=True)

# Derived environment chart. Cook Grate is preferred over Cook PID, never averaged.
derived_frames = []
if not cook_environment.empty:
    frame = cook_environment.rename(columns={"temperature_c": "Temperature °C"})
    frame["Derived environment"] = "Cook Environment"
    derived_frames.append(frame)
if not hold_environment.empty:
    frame = hold_environment.rename(columns={"temperature_c": "Temperature °C"})
    frame["Derived environment"] = "Hold Environment"
    derived_frames.append(frame)
if derived_frames:
    derived = pd.concat(derived_frames, ignore_index=True)
    st.plotly_chart(
        px.line(
            derived,
            x="timestamp",
            y="Temperature °C",
            color="Derived environment",
            line_dash="Source role",
            title="Derived Cook and Hold environments",
        ),
        use_container_width=True,
        key="derived_environment",
    )
else:
    st.info("No usable environment profile was available for the derived environment chart.")

# Raw all-source overlay
raw_frames = []
for label, valid in meat_data.items():
    frame = valid[["timestamp", "temperature_c"]].copy()
    frame.columns = ["timestamp", "Temperature °C"]
    frame["Temperature source"] = label
    raw_frames.append(frame)
for label, item in environment_profiles.items():
    frame = item["data"][["timestamp", "temperature_c"]].copy()
    frame.columns = ["timestamp", "Temperature °C"]
    frame["Temperature source"] = label
    raw_frames.append(frame)
if raw_frames:
    raw_overlay = pd.concat(raw_frames, ignore_index=True)
    raw_overlay.sort_values("timestamp", inplace=True)
    st.plotly_chart(
        px.line(
            raw_overlay,
            x="timestamp",
            y="Temperature °C",
            color="Temperature source",
            title="Raw meat and environment profiles",
        ),
        use_container_width=True,
        key="raw_overlay",
    )

# Detailed meat results
st.header("Meat-probe analysis")
for index, (label, result) in enumerate(meat_results.items()):
    stats = phase_stats_by_profile[label]
    sampling = sampling_info[label]
    with st.expander(f"{label} — {meat_sources[label]}", expanded=len(meat_results) == 1):
        row1 = st.columns(4)
        row1[0].metric("Session", result.detection.session_type)
        row1[1].metric("Cook duration", f"{stats['cook_hours']:.2f} h")
        row1[2].metric("Hold duration", f"{stats['hold_hours']:.2f} h")
        row1[3].metric(
            "Detected pull",
            result.detection.pull_timestamp.strftime("%d/%m/%Y %H:%M")
            if result.detection.pull_timestamp is not None else "Not detected",
        )
        row2 = st.columns(4)
        row2[0].metric("Cook contribution", f"{result.cook:.1%}")
        row2[1].metric("Hold contribution", f"{result.hold:.1%}")
        row2[2].metric("Recorded total", f"{result.total:.1%}")
        row2[3].metric("Assessment", result.assessment if result.complete else "Partial session")
        st.plotly_chart(
            px.line(meat_data[label], x="timestamp", y="temperature_c", title=label),
            use_container_width=True,
            key=f"meat_detail_{index}",
        )
        st.caption(
            f"Sampling: {format_interval(sampling['interval'])}. "
            f"Gap threshold: {format_interval(sampling['selected'])}."
        )

st.caption(
    "Cook PID and Cook Grate are assigned only to the smoking phase. Hold Environment "
    "is assigned only after transfer. Cook Grate is preferred over Cook PID for the "
    "derived Cook Environment. Raw profiles remain separate and are never averaged by default."
)
