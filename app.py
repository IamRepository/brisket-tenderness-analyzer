from __future__ import annotations

from io import BytesIO
import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit

APP_VERSION = "2.6.0"
POINT = "🥩 Brisket - Point"
FLAT = "🥩 Brisket - Flat"
COOK_PID = "🔥 Cook Environment - PID"
COOK_GRATE = "🌡 Cook Environment - Grate"
HOLD_ENV = "♨ Hold Environment - Probe"
IGNORE = "🚫 Ignore"
MEAT_ROLES = {POINT, FLAT}
ENVIRONMENT_ROLES = {COOK_PID, COOK_GRATE, HOLD_ENV}

st.set_page_config(page_title=f"Brisket Session Analyser {APP_VERSION}", page_icon="🔥", layout="wide")
st.title(f"🔥 Brisket Session Analyser {APP_VERSION}")
st.caption("Based on Steve Gow's brisket rendering and hot-hold methodology. Independent implementation; not affiliated with or endorsed by Steve Gow.")

with st.sidebar:
    st.header("Settings")
    with st.expander("Advanced sampling settings"):
        override_gap = st.checkbox("Override automatic gap detection", value=False)
        manual_gap = None
        if override_gap:
            manual_gap = st.number_input("Maximum accepted gap (seconds)", 1.0, 86400.0, 10.0, 1.0)

source = st.segmented_control("Data source", ["Upload file", "Try reference brisket"], default="Upload file")

@st.cache_data
def load_file(data: bytes, name: str):
    return engine.read_file(BytesIO(data), name)

def interval_text(seconds):
    if seconds is None:
        return "Unknown"
    if seconds < 60:
        return f"{seconds:.0f} sec"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"

def sampling_info(valid, override=None):
    gaps = valid["timestamp"].sort_values().diff().dt.total_seconds().dropna()
    gaps = gaps[gaps > 0]
    normal = float(gaps.median()) if not gaps.empty else None
    automatic = max(normal * 3.0, normal + 1.0) if normal is not None else 10.0
    return {
        "normal": normal,
        "threshold": float(override) if override is not None else automatic,
        "mode": "Manual override" if override is not None else "Automatic",
    }

def unique_label(role, file_label, column, existing):
    base = f"{role} — {column}"
    if base not in existing:
        return base
    return f"{role} — {file_label} / {column}"

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
        cook = work[work["timestamp"] < pd.Timestamp(pull)]
        hold = work[work["timestamp"] >= pd.Timestamp(pull)]
    else:
        cook, hold = work.iloc[0:0], work.iloc[0:0]

    def weighted(frame):
        if frame.empty or frame["elapsed"].sum() <= 0:
            return None
        return float((frame["temperature_c"] * frame["elapsed"]).sum() / frame["elapsed"].sum())

    return {
        "cook_h": float(cook["elapsed"].sum()) / 3600,
        "hold_h": float(hold["elapsed"].sum()) / 3600,
        "cook_avg": weighted(cook),
        "hold_avg": weighted(hold),
    }

# Step 1
st.header("Step 1: Upload temperature files")
if source == "Try reference brisket":
    primary_name = "Built-in reference brisket"
    primary_df = engine.demo_data()
    primary_time = "timestamp"
    secondary_name = None
    secondary_df = None
    secondary_time = None
    classifications = pd.DataFrame([{
        "Column": "Average Probe Temperature (°C)",
        "Suggested role": FLAT,
        "Confidence": 70,
        "File": "Primary",
    }])
else:
    primary = st.file_uploader("Primary temperature file", type=["xlsx", "xlsm", "xls", "csv"], key="primary")
    secondary = st.file_uploader("Secondary temperature file (optional)", type=["xlsx", "xlsm", "xls", "csv"], key="secondary")
    if primary is None:
        st.info("Upload a primary temperature file to begin.")
        st.stop()
    try:
        primary_name = primary.name
        sheets = load_file(primary.getvalue(), primary.name)
        sheet = st.selectbox("Primary worksheet", list(sheets), key="primary_sheet")
        primary_df = sheets[sheet]
        primary_time, _ = engine.detect_columns(primary_df)
        classifications = pit.classify_columns(primary_df, primary_time)
        classifications["File"] = "Primary"
        secondary_name = None
        secondary_df = None
        secondary_time = None
        if secondary is not None:
            secondary_name = secondary.name
            sheets2 = load_file(secondary.getvalue(), secondary.name)
            sheet2 = st.selectbox("Secondary worksheet", list(sheets2), key="secondary_sheet")
            secondary_df = sheets2[sheet2]
            secondary_time, _ = engine.detect_columns(secondary_df)
            classifications2 = pit.classify_columns(secondary_df, secondary_time)
            classifications2["File"] = "Secondary"
            classifications = pd.concat([classifications, classifications2], ignore_index=True)
    except Exception as exc:
        st.error(f"File setup failed: {exc}")
        st.stop()

if classifications.empty:
    st.error("No usable temperature columns were found.")
    st.stop()

# Step 2
st.header("Step 2: Assign probe and environment roles")
st.caption("Meat probes continue through Cook and Hold. Cook PID and Cook Grate belong to the smoker. Hold Environment belongs to the oven or holding equipment.")
roles = {}
for file_label, colour, icon in (("Primary", "#eaf3ff", "🟦"), ("Secondary", "#edf9ef", "🟩")):
    group = classifications[classifications["File"] == file_label]
    if group.empty:
        continue
    filename = primary_name if file_label == "Primary" else secondary_name
    st.markdown(f'<div style="background:{colour};padding:12px 16px;border-radius:10px;border:1px solid #d8dee6"><b>{icon} {file_label.upper()} FILE</b><br><small>{filename}</small></div>', unsafe_allow_html=True)
    for _, row in group.iterrows():
        key = f"{file_label}_{row['Column']}"
        suggested = row["Suggested role"]
        index = pit.ROLES.index(suggested) if suggested in pit.ROLES else pit.ROLES.index(IGNORE)
        roles[key] = st.selectbox(f"{row['Column']} ({row['Confidence']}% suggested confidence)", pit.ROLES, index=index, key=f"role_{key}")

configuration = []
for _, row in classifications.iterrows():
    key = f"{row['File']}_{row['Column']}"
    if roles[key] != IGNORE:
        configuration.append({"Role": roles[key], "Source": f"{row['File']} / {row['Column']}"})
st.subheader("Detected configuration")
st.dataframe(pd.DataFrame(configuration), hide_index=True, use_container_width=True)

if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()

# Analyse
meat_results = {}
environment_results = {}
errors = []
for _, row in classifications.iterrows():
    file_label, column = row["File"], row["Column"]
    role = roles[f"{file_label}_{column}"]
    if role == IGNORE:
        continue
    active_df = primary_df if file_label == "Primary" else secondary_df
    active_time = primary_time if file_label == "Primary" else secondary_time
    try:
        if role in MEAT_ROLES:
            valid, report = engine.prepare(active_df, active_time, column)
            detection = engine.classify_session(valid)
            sample = sampling_info(valid, manual_gap)
            result = engine.analyse(valid, report, detection, max_gap=sample["threshold"])
            label = unique_label(role, file_label, str(column), meat_results)
            meat_results[label] = {"result": result, "valid": valid, "source": f"{file_label} / {column}", "sample": sample}
        elif role in ENVIRONMENT_ROLES:
            prepared = pit.prepare(active_df, active_time, column)
            label = f"{role} — {column}"
            environment_results[label] = pit.analyse(prepared, role)
    except Exception as exc:
        errors.append(f"{file_label} / {column}: {exc}")

for error in errors:
    st.error(error)
if not meat_results:
    st.error("At least one column must be classified as Brisket - Point or Brisket - Flat.")
    st.stop()

# Summary
st.header("Session detection summary")
summary = []
stats = {}
for label, item in meat_results.items():
    result = item["result"]
    stat = phase_stats(item["valid"], result.detection)
    stats[label] = stat
    summary.append({
        "Profile": label,
        "Source": item["source"],
        "Session type": result.detection.session_type,
        "Confidence": f"{result.detection.confidence}%",
        "Detected pull": result.detection.pull_timestamp,
        "Peak °C": round(result.detection.peak_temperature, 1),
        "Average °C": round(result.detection.average_temperature, 1),
        "Minimum °C": round(result.detection.minimum_temperature, 1),
        "Cook hours": round(stat["cook_h"], 2),
        "Hold hours": round(stat["hold_h"], 2),
        "Average cook °C": None if stat["cook_avg"] is None else round(stat["cook_avg"], 1),
        "Average hold °C": None if stat["hold_avg"] is None else round(stat["hold_avg"], 1),
        "Sampling interval": interval_text(item["sample"]["normal"]),
        "Gap threshold": interval_text(item["sample"]["threshold"]),
    })
st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)

# Probe comparison
st.header("Brisket probe comparison")
comparison_rows, frames = [], []
for label, item in meat_results.items():
    result = item["result"]
    comparison_rows.append({
        "Profile": label,
        "Source": item["source"],
        "Cook contribution": f"{result.cook:.1%}",
        "Hold contribution": f"{result.hold:.1%}",
        "Recorded total": f"{result.total:.1%}",
        "Assessment": result.assessment if result.complete else "Partial session",
        "Analysed hours": round(result.analysed_hours, 2),
    })
    frame = item["valid"][["timestamp", "temperature_c"]].copy()
    frame.columns = ["Timestamp", "Temperature °C"]
    frame["Profile"] = label
    frames.append(frame)
st.dataframe(pd.DataFrame(comparison_rows), hide_index=True, use_container_width=True)
combined = pd.concat(frames, ignore_index=True)
st.plotly_chart(px.line(combined, x="Timestamp", y="Temperature °C", color="Profile", title="Brisket - Point and Brisket - Flat"), use_container_width=True, key="brisket_comparison")

# Detailed meat profiles
st.header("Brisket probe analysis")
for index, (label, item) in enumerate(meat_results.items()):
    result, stat, sample = item["result"], stats[label], item["sample"]
    with st.expander(f"{label} — {item['source']}", expanded=len(meat_results) == 1):
        cols = st.columns(4)
        cols[0].metric("Session", result.detection.session_type)
        cols[1].metric("Confidence", f"{result.detection.confidence}%")
        cols[2].metric("Detected pull", result.detection.pull_timestamp.strftime("%d/%m/%Y %H:%M") if result.detection.pull_timestamp is not None else "Not detected")
        cols[3].metric("Peak", f"{result.detection.peak_temperature:.1f}°C")
        cols = st.columns(4)
        cols[0].metric("Cook contribution", f"{result.cook:.1%}")
        cols[1].metric("Hold contribution", f"{result.hold:.1%}")
        cols[2].metric("Recorded total", f"{result.total:.1%}")
        cols[3].metric("Assessment", result.assessment if result.complete else "Partial session")
        cols = st.columns(4)
        cols[0].metric("Cook duration", f"{stat['cook_h']:.2f} h")
        cols[1].metric("Hold duration", f"{stat['hold_h']:.2f} h")
        cols[2].metric("Average cook", "N/A" if stat["cook_avg"] is None else f"{stat['cook_avg']:.1f}°C")
        cols[3].metric("Average hold", "N/A" if stat["hold_avg"] is None else f"{stat['hold_avg']:.1f}°C")
        tabs = st.tabs(["Temperature", "Accumulated rendering", "Band calculation", "Data quality"])
        with tabs[0]:
            frame = item["valid"][["timestamp", "temperature_c"]].copy()
            frame.columns = ["Timestamp", "Temperature °C"]
            st.plotly_chart(px.line(frame, x="Timestamp", y="Temperature °C", title=label), use_container_width=True, key=f"temperature_{index}")
        with tabs[1]:
            chart = px.line(result.timeline, x="Timestamp", y="Accumulated rendering")
            chart.update_yaxes(tickformat=".0%")
            st.plotly_chart(chart, use_container_width=True, key=f"rendering_{index}")
        with tabs[2]:
            bands = result.summary[result.summary["Duration hours"] > 0].copy()
            bands["Duration hours"] = bands["Duration hours"].round(3)
            bands["Rate per hour"] = bands["Rate per hour"].map(lambda value: f"{value:.1%}")
            bands["Tenderness contribution"] = bands["Tenderness contribution"].map(lambda value: f"{value:.1%}")
            st.dataframe(bands, hide_index=True, use_container_width=True, key=f"bands_{index}")
        with tabs[3]:
            cols = st.columns(3)
            cols[0].metric("Analysed hours", f"{result.analysed_hours:.3f}")
            cols[1].metric("Excluded gap hours", f"{result.excluded_gap_hours:.3f}")
            cols[2].metric("Below 60°C hours", f"{result.below_model_hours:.3f}")
            st.write(f"Sampling: {sample['mode']} | Normal interval: {interval_text(sample['normal'])} | Gap threshold: {interval_text(sample['threshold'])}")

# Environment streams
if environment_results:
    st.header("Environment analysis")
    overlay_frames = []
    for label, item in meat_results.items():
        frame = item["valid"][["timestamp", "temperature_c"]].copy()
        frame.columns = ["timestamp", "Temperature °C"]
        frame["Temperature source"] = label
        overlay_frames.append(frame)
    for index, (label, result) in enumerate(environment_results.items()):
        with st.expander(label, expanded=True):
            cols = st.columns(4)
            cols[0].metric("Average", f"{result.average:.1f}°C")
            cols[1].metric("Minimum", f"{result.minimum:.1f}°C")
            cols[2].metric("Maximum", f"{result.maximum:.1f}°C")
            cols[3].metric("Stability", f"{result.stability_score:.0f}/100")
            st.plotly_chart(px.line(result.timeline, x="timestamp", y="temperature_c", title=label), use_container_width=True, key=f"environment_{index}")
        frame = result.timeline[["timestamp", "temperature_c"]].copy()
        frame.columns = ["timestamp", "Temperature °C"]
        frame["Temperature source"] = label
        overlay_frames.append(frame)
    overlay = pd.concat(overlay_frames, ignore_index=True)
    overlay.sort_values("timestamp", inplace=True)
    st.plotly_chart(px.line(overlay, x="timestamp", y="Temperature °C", color="Temperature source", title="Brisket and environment overlay"), use_container_width=True, key="environment_overlay")

st.caption("Sampling and gap thresholds are automatically detected unless manually overridden. Environment stability and event detections are analytical estimates, not manufacturer-provided Weber metrics.")
