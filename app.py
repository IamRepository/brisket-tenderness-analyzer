from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit

APP_VERSION = "2.4.4"
MEAT_ROLES = ("🥩 Point", "🥩 Flat", "🍖 Other Meat")
ENVIRONMENT_ROLES = ("🌡 Grate", "🔥 PID")
IGNORE_ROLE = "🚫 Ignore"

st.set_page_config(page_title=f"Brisket Session Analyser {APP_VERSION}", page_icon="🔥", layout="wide")
st.title(f"🔥 Brisket Session Analyser {APP_VERSION}")
st.caption("Based on Steve Gow's brisket rendering and hot-hold methodology. Independent implementation; not affiliated with or endorsed by Steve Gow.")

with st.sidebar:
    st.header("Settings")
    with st.expander("Advanced sampling settings"):
        manual_override = st.checkbox(
            "Override automatic gap detection",
            value=False,
            help="Leave this off for normal use. The analyser detects the file's normal sampling interval automatically.",
        )
        manual_gap = None
        if manual_override:
            manual_gap = st.number_input("Maximum accepted gap (seconds)", 1.0, 86400.0, 10.0, 1.0)

source = st.segmented_control("Data source", ["Upload file", "Try reference brisket"], default="Upload file")


@st.cache_data
def load_file(data, name):
    return engine.read_file(BytesIO(data), name)


def format_interval(seconds):
    if seconds is None:
        return "Unknown"
    if seconds < 60:
        return f"{seconds:.0f} sec"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"


def sampling_settings(valid, override=None):
    intervals = valid["timestamp"].sort_values().diff().dt.total_seconds().dropna()
    intervals = intervals[intervals > 0]
    detected = float(intervals.median()) if len(intervals) else None
    automatic = max(detected * 3.0, detected + 1.0) if detected is not None else 10.0
    selected = float(override) if override is not None else automatic
    return {"interval": detected, "automatic": automatic, "selected": selected, "manual": override is not None}


def source_label(file_role, column):
    return f"{file_role} / {column}"


def clean_role(role):
    return {
        "🥩 Point": "Point",
        "🥩 Flat": "Flat",
        "🍖 Other Meat": "Other Meat",
        "🌡 Grate": "Grate",
        "🔥 PID": "PID",
    }.get(role, role)


def unique_profile_label(role, file_role, column, current):
    base = f"{clean_role(role)} — {column}"
    if base not in current:
        return base
    candidate = f"{clean_role(role)} — {file_role} / {column}"
    if candidate not in current:
        return candidate
    n = 2
    while f"{candidate} ({n})" in current:
        n += 1
    return f"{candidate} ({n})"


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

    def weighted_temp(frame):
        if frame.empty:
            return None
        return float((frame["temperature_c"] * frame["elapsed"]).sum() / frame["elapsed"].sum())

    return {
        "cook_hours": float(cook["elapsed"].sum()) / 3600,
        "hold_hours": float(hold["elapsed"].sum()) / 3600,
        "cook_temp": weighted_temp(cook),
        "hold_temp": weighted_temp(hold),
    }


# Upload and discovery
if source == "Try reference brisket":
    primary_name = "Reference brisket"
    df = engine.demo_data()
    timestamp_col = "timestamp"
    classifications = pd.DataFrame([{
        "Column": "Average Probe Temperature (°C)",
        "Suggested role": "🍖 Other Meat",
        "Confidence": 99,
        "File": "Reference",
    }])
    extra_df = extra_timestamp = optional_name = None
else:
    st.subheader("Step 1: Upload temperature files")
    primary = st.file_uploader("Primary temperature file", type=["xlsx", "xlsm", "xls", "csv"], key="primary")
    optional = st.file_uploader("Secondary temperature file (optional)", type=["xlsx", "xlsm", "xls", "csv"], key="secondary")
    if primary is None:
        st.info("Upload a primary temperature file to begin.")
        st.stop()

    primary_name = primary.name
    sheets = load_file(primary.getvalue(), primary.name)
    sheet = st.selectbox("Primary worksheet", list(sheets), key="primary_sheet")
    df = sheets[sheet]
    timestamp_col, _ = engine.detect_columns(df)
    classifications = pit.classify_columns(df, timestamp_col)
    classifications["File"] = "Primary"
    extra_df = extra_timestamp = optional_name = None

    if optional is not None:
        optional_name = optional.name
        osheets = load_file(optional.getvalue(), optional.name)
        osheet = st.selectbox("Secondary worksheet", list(osheets), key="secondary_sheet")
        extra_df = osheets[osheet]
        extra_timestamp, _ = engine.detect_columns(extra_df)
        extra = pit.classify_columns(extra_df, extra_timestamp)
        extra["File"] = "Secondary"
        classifications = pd.concat([classifications, extra], ignore_index=True)

# Role mapping
st.subheader("Step 2: Assign probe roles")
st.caption("Point, Flat and Other Meat are analysed independently. PID and Grate are treated as cooking-environment profiles.")
roles = {}
for file_role, colour, icon in (("Primary", "#eaf3ff", "🟦"), ("Secondary", "#edf9ef", "🟩"), ("Reference", "#fff6df", "🟨")):
    group = classifications[classifications["File"] == file_role]
    if group.empty:
        continue
    display_name = primary_name if file_role in ("Primary", "Reference") else optional_name
    st.markdown(f'<div style="background:{colour};padding:12px 16px;border-radius:10px;border:1px solid #d8dee6;margin-top:12px"><b>{icon} {file_role.upper()} FILE</b><br><small>{display_name}</small></div>', unsafe_allow_html=True)
    for _, row in group.iterrows():
        key = f"{file_role}_{row['Column']}"
        suggested = row["Suggested role"]
        index = pit.ROLES.index(suggested) if suggested in pit.ROLES else pit.ROLES.index(IGNORE_ROLE)
        roles[key] = st.selectbox(f"{row['Column']} ({row['Confidence']}% suggested confidence)", pit.ROLES, index=index, key=f"role_{key}")

config = []
for _, row in classifications.iterrows():
    key = f"{row['File']}_{row['Column']}"
    if roles[key] != IGNORE_ROLE:
        config.append({"Role": roles[key], "Source": source_label(row["File"], row["Column"])})
st.markdown("#### Detected configuration")
st.dataframe(pd.DataFrame(config), hide_index=True, use_container_width=True)

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()

# Analysis
meat_results, meat_data, meat_sources, sample_info, environment_results = {}, {}, {}, {}, {}
for _, row in classifications.iterrows():
    file_role, column = row["File"], row["Column"]
    role = roles[f"{file_role}_{column}"]
    if role == IGNORE_ROLE:
        continue
    active_df = df if file_role in ("Primary", "Reference") else extra_df
    active_time = timestamp_col if file_role in ("Primary", "Reference") else extra_timestamp

    if role in MEAT_ROLES:
        valid, report = engine.prepare(active_df, active_time, column)
        detection = engine.classify_session(valid)
        sampling = sampling_settings(valid, manual_gap)
        label = unique_profile_label(role, file_role, str(column), meat_results)
        result = engine.analyse(valid, report, detection, max_gap=sampling["selected"])
        meat_results[label], meat_data[label] = result, valid
        meat_sources[label], sample_info[label] = source_label(file_role, column), sampling
    elif role in ENVIRONMENT_ROLES:
        prepared = pit.prepare(active_df, active_time, column)
        label = f"{clean_role(role)} — {column}"
        environment_results[label] = pit.analyse(prepared, role)

if not meat_results:
    st.error("At least one column must be classified as Point, Flat or Other Meat.")
    st.stop()

# Summary
st.subheader("Session detection summary")
summary = []
stats = {}
for label, result in meat_results.items():
    detection = result.detection
    stat = phase_stats(meat_data[label], detection)
    stats[label] = stat
    sampling = sample_info[label]
    summary.append({
        "Profile": label,
        "Source": meat_sources[label],
        "Session type": detection.session_type,
        "Confidence": f"{detection.confidence}%",
        "Detected pull": detection.pull_timestamp,
        "Peak °C": round(detection.peak_temperature, 1),
        "Average °C": round(detection.average_temperature, 1),
        "Minimum °C": round(detection.minimum_temperature, 1),
        "Cook hours": round(stat["cook_hours"], 2),
        "Hold hours": round(stat["hold_hours"], 2),
        "Average cook °C": None if stat["cook_temp"] is None else round(stat["cook_temp"], 1),
        "Average hold °C": None if stat["hold_temp"] is None else round(stat["hold_temp"], 1),
        "Sampling interval": format_interval(sampling["interval"]),
        "Gap threshold": format_interval(sampling["selected"]),
    })
st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)

# Comparison
st.subheader("Multi-probe comparison")
comparisons, frames = [], []
for label, result in meat_results.items():
    comparisons.append({
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
    frames.append(frame)
st.dataframe(pd.DataFrame(comparisons), hide_index=True, use_container_width=True)
combined = pd.concat(frames, ignore_index=True)
fig = px.line(combined, x="Timestamp", y="Temperature °C", color="Profile", title="All classified meat probes")
st.plotly_chart(fig, use_container_width=True, key="comparison_chart")

# Detail by profile
st.subheader("Meat-probe analysis")
for i, (label, result) in enumerate(meat_results.items()):
    detection, stat, sampling = result.detection, stats[label], sample_info[label]
    with st.expander(f"{label} — {meat_sources[label]}", expanded=len(meat_results) == 1):
        cols = st.columns(4)
        cols[0].metric("Session", detection.session_type)
        cols[1].metric("Confidence", f"{detection.confidence}%")
        cols[2].metric("Detected pull", detection.pull_timestamp.strftime("%d/%m/%Y %H:%M") if detection.pull_timestamp is not None else "Not detected")
        cols[3].metric("Peak", f"{detection.peak_temperature:.1f}°C")
        cols = st.columns(4)
        cols[0].metric("Cook contribution", f"{result.cook:.1%}")
        cols[1].metric("Hold contribution", f"{result.hold:.1%}")
        cols[2].metric("Recorded total", f"{result.total:.1%}")
        cols[3].metric("Assessment", result.assessment if result.complete else "Partial session")
        cols = st.columns(4)
        cols[0].metric("Cook duration", f"{stat['cook_hours']:.2f} h")
        cols[1].metric("Hold duration", f"{stat['hold_hours']:.2f} h")
        cols[2].metric("Average cook", "N/A" if stat["cook_temp"] is None else f"{stat['cook_temp']:.1f}°C")
        cols[3].metric("Average hold", "N/A" if stat["hold_temp"] is None else f"{stat['hold_temp']:.1f}°C")

        tabs = st.tabs(["Temperature", "Accumulated rendering", "Band calculation", "Data quality"])
        with tabs[0]:
            frame = meat_data[label][["timestamp", "temperature_c"]].copy()
            frame.columns = ["Timestamp", "Temperature °C"]
            st.plotly_chart(px.line(frame, x="Timestamp", y="Temperature °C", title=label), use_container_width=True, key=f"temp_{i}")
        with tabs[1]:
            chart = px.line(result.timeline, x="Timestamp", y="Accumulated rendering")
            chart.update_yaxes(tickformat=".0%")
            st.plotly_chart(chart, use_container_width=True, key=f"render_{i}")
        with tabs[2]:
            table = result.summary[result.summary["Duration hours"] > 0].copy()
            table["Duration hours"] = table["Duration hours"].round(3)
            table["Rate per hour"] = table["Rate per hour"].map(lambda x: f"{x:.1%}")
            table["Tenderness contribution"] = table["Tenderness contribution"].map(lambda x: f"{x:.1%}")
            st.dataframe(table, hide_index=True, use_container_width=True, key=f"bands_{i}")
        with tabs[3]:
            cols = st.columns(3)
            cols[0].metric("Analysed hours", f"{result.analysed_hours:.3f}")
            cols[1].metric("Excluded gap hours", f"{result.excluded_gap_hours:.3f}")
            cols[2].metric("Below 60°C hours", f"{result.below_model_hours:.3f}")
            mode = "Manual override" if sampling["manual"] else "Automatic"
            st.write(f"Sampling: {mode} | Normal interval: {format_interval(sampling['interval'])} | Gap threshold: {format_interval(sampling['selected'])}")
            st.write("Valid / invalid / duplicates:", result.report.valid_rows, result.report.invalid_rows, result.report.duplicate_timestamps)

# Environment
if environment_results:
    st.subheader("Cooking-environment analysis")
    overlay_frames = []
    for label, valid in meat_data.items():
        frame = valid[["timestamp", "temperature_c"]].copy()
        frame.columns = ["timestamp", "Temperature °C"]
        frame["Temperature source"] = label
        overlay_frames.append(frame)
    for i, (label, result) in enumerate(environment_results.items()):
        with st.expander(label, expanded=True):
            cols = st.columns(4)
            cols[0].metric("Average", f"{result.average:.1f}°C")
            cols[1].metric("Minimum", f"{result.minimum:.1f}°C")
            cols[2].metric("Maximum", f"{result.maximum:.1f}°C")
            cols[3].metric("Stability", f"{result.stability_score:.0f}/100")
            st.plotly_chart(px.line(result.timeline, x="timestamp", y="temperature_c", title=label), use_container_width=True, key=f"env_{i}")
        frame = result.timeline[["timestamp", "temperature_c"]].copy()
        frame.columns = ["timestamp", "Temperature °C"]
        frame["Temperature source"] = label
        overlay_frames.append(frame)
    overlay = pd.concat(overlay_frames, ignore_index=True)
    overlay.sort_values("timestamp", inplace=True)
    st.plotly_chart(px.line(overlay, x="timestamp", y="Temperature °C", color="Temperature source", title="Meat and cooking-environment overlay"), use_container_width=True, key="overlay")

st.caption("Sampling and gap thresholds are automatically detected unless manually overridden. Pit stability and event detections are analytical estimates, not manufacturer-provided Weber metrics.")
