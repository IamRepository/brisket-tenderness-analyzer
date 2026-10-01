from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit
from pdf_report import build_pdf_report

APP_VERSION = "2.6.8"
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
    st.divider()
    report_slot = st.empty()
    report_slot.caption("Run an analysis to enable the full PDF report.")

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


def display_source(value):
    """Normalise known source-header spelling without repeated substitutions."""
    text = str(value).strip()
    if "/" not in text:
        return text

    file_label, column = [part.strip() for part in text.split("/", 1)]
    lowered = column.lower()

    # Accept Poin, Point, Pointt, Pointtt, etc., but do not alter other names.
    if lowered.startswith("poin") and set(lowered[4:]) <= {"t"}:
        column = "Point"
    elif lowered == "enviroment":
        column = "Environment"

    return f"{file_label} / {column}"


def stage_display_label(label, result):
    """Return a concise role and stage label for charts."""
    role = str(label).split(" — ", 1)[0]
    role = role.replace("🥩 ", "")
    if result.detection.session_type == "Cook + Hold":
        return f"{role} (Cook + Hold)"
    if result.detection.session_type in ("Hold Only", "Calibration / Hold Test"):
        return f"{role} (Hold only)"
    if result.detection.session_type == "Cook Only":
        return f"{role} (Cook only)"
    return f"{role} ({result.detection.session_type})"


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
        pull = pd.Timestamp(pull)
        cook = work[work["timestamp"] < pull]
        hold = work[work["timestamp"] >= pull]
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


def derive_transfer_time(meat_results):
    """Derive transfer only from full Cook + Hold profiles.

    Secondary hold-only or calibration probe streams are excluded because
    their peaks do not represent the smoker pull.
    """
    pulls = sorted(
        pd.Timestamp(item["result"].detection.pull_timestamp)
        for item in meat_results.values()
        if item["result"].detection.session_type == "Cook + Hold"
        and item["result"].detection.pull_timestamp is not None
    )
    if not pulls:
        pulls = sorted(
            pd.Timestamp(item["result"].detection.pull_timestamp)
            for item in meat_results.values()
            if item["result"].detection.pull_timestamp is not None
        )
    if not pulls:
        return None
    middle = len(pulls) // 2
    if len(pulls) % 2:
        return pulls[middle]
    return pulls[middle - 1] + (pulls[middle] - pulls[middle - 1]) / 2


def segment_environment(prepared, role, transfer_time):
    """Limit cook sensors to pre-transfer and hold sensors to post-transfer data."""
    if transfer_time is None:
        return prepared.copy()
    if role in {COOK_PID, COOK_GRATE}:
        return prepared[prepared["timestamp"] < transfer_time].copy()
    if role == HOLD_ENV:
        return prepared[prepared["timestamp"] >= transfer_time].copy()
    return prepared.copy()


def stream_interval_seconds(frame):
    intervals = frame["timestamp"].sort_values().diff().dt.total_seconds().dropna()
    intervals = intervals[intervals > 0]
    return float(intervals.median()) if not intervals.empty else None


def align_environment_to_reference(reference, streams, phase_start=None, phase_end=None):
    """Align environment streams without extending data beyond actual coverage.

    Nearest-time matching is allowed only inside each sensor's first and last
    recorded timestamp. This prevents a later Hold reading from being assigned
    backwards to an earlier reference timestamp.
    """
    aligned = reference[["timestamp"]].drop_duplicates().sort_values("timestamp").copy()
    if phase_start is not None:
        aligned = aligned[aligned["timestamp"] >= phase_start]
    if phase_end is not None:
        aligned = aligned[aligned["timestamp"] < phase_end]

    columns = []
    coverage_starts = []
    coverage_ends = []

    for label, result in streams.items():
        stream = result.timeline[["timestamp", "temperature_c"]].dropna().sort_values("timestamp")
        if stream.empty:
            continue

        stream_start = pd.Timestamp(stream["timestamp"].min())
        stream_end = pd.Timestamp(stream["timestamp"].max())
        coverage_starts.append(stream_start)
        coverage_ends.append(stream_end)

        interval = stream_interval_seconds(stream)
        tolerance = max((interval or 30.0) * 1.5, 30.0)
        stream = stream.rename(columns={"temperature_c": label})
        aligned = pd.merge_asof(
            aligned.sort_values("timestamp"),
            stream,
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta(seconds=tolerance),
        )

        outside_coverage = ~aligned["timestamp"].between(
            stream_start, stream_end, inclusive="both"
        )
        aligned.loc[outside_coverage, label] = np.nan
        columns.append(label)

    if not columns:
        return pd.DataFrame()

    # Remove reference timestamps outside the union of actual stream coverage.
    coverage_start = min(coverage_starts)
    coverage_end = max(coverage_ends)
    aligned = aligned[
        aligned["timestamp"].between(coverage_start, coverage_end, inclusive="both")
    ].copy()

    aligned["Environment aggregate °C"] = aligned[columns].mean(axis=1, skipna=True)
    aligned["Available sensors"] = aligned[columns].notna().sum(axis=1)
    return aligned[aligned["Available sensors"] > 0].reset_index(drop=True)


def environment_coverage(full_frame, segmented_frame, role, boundary):
    return {
        "Role": role,
        "Total readings": len(full_frame),
        "Retained readings": len(segmented_frame),
        "Excluded outside phase": max(len(full_frame) - len(segmented_frame), 0),
        "Phase boundary": boundary,
    }


def duration_hours(frame):
    if frame is None or len(frame) < 2:
        return 0.0
    return max((frame["timestamp"].iloc[-1] - frame["timestamp"].iloc[0]).total_seconds() / 3600, 0.0)


def align_hold_delta(hold_frame, meat_frame, label):
    """Align one meat stream to the hold environment without interpolation."""
    if hold_frame.empty or meat_frame.empty:
        return pd.DataFrame()
    env = hold_frame[["timestamp", "temperature_c"]].rename(columns={"temperature_c": "Hold environment °C"}).sort_values("timestamp")
    meat = meat_frame[["timestamp", "temperature_c"]].rename(columns={"temperature_c": "Meat °C"}).sort_values("timestamp")
    intervals = env["timestamp"].diff().dt.total_seconds().dropna()
    tolerance = max(float(intervals[intervals > 0].median()) * 2, 60.0) if (intervals > 0).any() else 60.0
    merged = pd.merge_asof(meat, env, on="timestamp", direction="nearest", tolerance=pd.Timedelta(seconds=tolerance))
    merged.dropna(subset=["Hold environment °C"], inplace=True)
    merged["Environment minus meat °C"] = merged["Hold environment °C"] - merged["Meat °C"]
    merged["Profile"] = label
    return merged


def derive_master_start(meat_results, environment_results):
    """Return the earliest timestamp across every analysed stream."""
    starts = []
    for item in meat_results.values():
        valid = item.get("valid")
        if valid is not None and not valid.empty:
            starts.append(pd.Timestamp(valid["timestamp"].min()))
    for result in environment_results.values():
        if result.timeline is not None and not result.timeline.empty:
            starts.append(pd.Timestamp(result.timeline["timestamp"].min()))
    return min(starts) if starts else None


# Step 1
st.header("Step 1: Upload temperature files")
if source == "Try reference brisket":
    primary_name = "Built-in reference brisket"
    primary_df = engine.demo_data()
    primary_time = "timestamp"
    secondary_name = secondary_df = secondary_time = None
    classifications = pd.DataFrame([{"Column": "Average Probe Temperature (°C)", "Suggested role": FLAT, "Confidence": 70, "File": "Primary"}])
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
        secondary_name = secondary_df = secondary_time = None
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
        suggestion = row["Suggested role"]
        index = pit.ROLES.index(suggestion) if suggestion in pit.ROLES else pit.ROLES.index(IGNORE)
        roles[key] = st.selectbox(f"{row['Column']} ({row['Confidence']}% suggested confidence)", pit.ROLES, index=index, key=f"role_{key}")

config = []
for _, row in classifications.iterrows():
    role = roles[f"{row['File']}_{row['Column']}"]
    if role != IGNORE:
        config.append({"Role": role, "Source": display_source(f"{row['File']} / {row['Column']}")})
st.subheader("Detected configuration")
st.dataframe(pd.DataFrame(config), hide_index=True, use_container_width=True)
if not st.button("Analyse session", type="primary", use_container_width=True):
    st.stop()

# Meat first, so transfer time is known before environment segmentation.
meat_results = {}
environment_inputs = []
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
            meat_results[label] = {"result": result, "valid": valid, "source": display_source(f"{file_label} / {column}"), "sample": sample}
        elif role in ENVIRONMENT_ROLES:
            environment_inputs.append({"role": role, "file": file_label, "column": column, "df": active_df, "time": active_time})
    except Exception as exc:
        errors.append(f"{file_label} / {column}: {exc}")

for error in errors:
    st.error(error)
if not meat_results:
    st.error("At least one column must be classified as Brisket - Point or Brisket - Flat.")
    st.stop()

transfer_time = derive_transfer_time(meat_results)
environment_results = {}
environment_sources = {}
environment_full = {}
environment_warnings = []
environment_coverage_rows = []
for item in environment_inputs:
    try:
        prepared = pit.prepare(item["df"], item["time"], item["column"])
        segmented = segment_environment(prepared, item["role"], transfer_time)
        label = f"{item['role']} — {item['column']}"
        environment_full[label] = prepared
        environment_coverage_rows.append(
            environment_coverage(prepared, segmented, item["role"], transfer_time)
        )
        if segmented.empty:
            environment_warnings.append(
                f"{label}: no readings overlap the applicable Cook/Hold phase."
            )
            continue
        environment_results[label] = pit.analyse(segmented, item["role"])
        environment_sources[label] = display_source(f"{item['file']} / {item['column']}")
    except Exception as exc:
        environment_warnings.append(
            f"{item['file']} / {item['column']}: {exc}"
        )
for warning in environment_warnings:
    st.warning(warning)

# Session summary
st.header("Session detection summary")
if transfer_time is not None:
    st.info(f"Derived smoker-to-hold transfer boundary: {transfer_time:%d/%m/%Y %H:%M:%S}. This is the median detected pull time across the brisket probes.")
summary, stats = [], {}
for label, item in meat_results.items():
    result = item["result"]
    stat = phase_stats(item["valid"], result.detection)
    stats[label] = stat
    summary.append({"Profile": label, "Source": item["source"], "Session type": result.detection.session_type, "Confidence": f"{result.detection.confidence}%", "Detected pull": result.detection.pull_timestamp, "Peak °C": round(result.detection.peak_temperature, 1), "Average °C": round(result.detection.average_temperature, 1), "Minimum °C": round(result.detection.minimum_temperature, 1), "Cook hours": round(stat["cook_h"], 2), "Hold hours": round(stat["hold_h"], 2), "Average cook °C": None if stat["cook_avg"] is None else round(stat["cook_avg"], 1), "Average hold °C": None if stat["hold_avg"] is None else round(stat["hold_avg"], 1), "Sampling interval": interval_text(item["sample"]["normal"]), "Gap threshold": interval_text(item["sample"]["threshold"])})
st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)

# Probe comparison
st.header("Brisket probe comparison")
rows, frames = [], []
for label, item in meat_results.items():
    result = item["result"]
    rows.append({"Profile": stage_display_label(label, result), "Source": item["source"], "Cook contribution": f"{result.cook:.1%}", "Hold contribution": f"{result.hold:.1%}", "Recorded total": f"{result.total:.1%}", "Assessment": result.assessment if result.complete else "Partial session", "Analysed hours": round(result.analysed_hours, 2)})
    frame = item["valid"][["timestamp", "temperature_c"]].copy(); frame.columns = ["Timestamp", "Temperature °C"]; frame["Profile"] = stage_display_label(label, result); frames.append(frame)
st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
combined = pd.concat(frames, ignore_index=True)
st.plotly_chart(px.line(combined, x="Timestamp", y="Temperature °C", color="Profile", title="Brisket - Point and Brisket - Flat"), use_container_width=True, key="brisket_comparison")

# Gap 1: explicit phase segmentation.
if environment_coverage_rows:
    st.header("Environment phase segmentation")
    st.caption(
        "Cook Environment readings are retained only before transfer. "
        "Hold Environment readings are retained only from transfer onwards."
    )
    st.dataframe(pd.DataFrame(environment_coverage_rows), hide_index=True, use_container_width=True)

# Gap 2: phase-aware alignment and aggregation.
cook_aggregate = pd.DataFrame()
hold_aggregate = pd.DataFrame()
composite_environment = pd.DataFrame()
if environment_results:
    reference = pd.concat(
        [item["valid"][["timestamp"]] for item in meat_results.values()],
        ignore_index=True,
    ).drop_duplicates().sort_values("timestamp")
    cook_streams = {k: v for k, v in environment_results.items() if v.role in {COOK_PID, COOK_GRATE}}
    hold_streams = {k: v for k, v in environment_results.items() if v.role == HOLD_ENV}
    cook_aggregate = align_environment_to_reference(reference, cook_streams, phase_end=transfer_time)
    hold_aggregate = align_environment_to_reference(reference, hold_streams, phase_start=transfer_time)

    st.header("Phase-aware environment aggregate")
    aggregate_rows = []
    aggregate_frames = []
    for phase, frame in (("Cook", cook_aggregate), ("Hold", hold_aggregate)):
        if frame.empty:
            continue
        aggregate_rows.append({
            "Phase": phase,
            "Start": frame["timestamp"].min(),
            "End": frame["timestamp"].max(),
            "Aligned readings": len(frame),
            "Average environment °C": round(frame["Environment aggregate °C"].mean(), 1),
            "Average available sensors": round(frame["Available sensors"].mean(), 2),
        })
        view = frame[["timestamp", "Environment aggregate °C"]].copy()
        view["Phase"] = f"{phase} Environment aggregate"
        aggregate_frames.append(view)
    if aggregate_rows:
        st.dataframe(pd.DataFrame(aggregate_rows), hide_index=True, use_container_width=True)
        composite_environment = pd.concat(aggregate_frames, ignore_index=True)
        composite_environment.rename(columns={"Phase": "Environment stage"}, inplace=True)
        st.plotly_chart(
            px.line(
                composite_environment, x="timestamp", y="Environment aggregate °C",
                color="Environment stage", title="Composite environment temperature",
            ),
            use_container_width=True, key="phase_environment_aggregate",
        )
    else:
        st.info("No environment readings could be aligned within their applicable phases.")

# Environment analysis including Hold Environment.
if environment_results:
    st.header("Environment analysis")
    env_summary = []
    for label, result in environment_results.items():
        phase = "Hold" if result.role == HOLD_ENV else "Cook"
        env_summary.append({"Environment stream": label, "Applicable phase": phase, "Start": result.timeline["timestamp"].min(), "End": result.timeline["timestamp"].max(), "Duration hours": round(duration_hours(result.timeline), 2), "Average °C": round(result.average, 1), "Minimum °C": round(result.minimum, 1), "Maximum °C": round(result.maximum, 1), "Stability": round(result.stability_score)})
    st.dataframe(pd.DataFrame(env_summary), hide_index=True, use_container_width=True)

    hold_results = {k: v for k, v in environment_results.items() if v.role == HOLD_ENV}
    if hold_results:
        st.subheader("Hold Environment analysis")
        for env_index, (env_label, env_result) in enumerate(hold_results.items()):
            cols = st.columns(4)
            cols[0].metric("Average hold environment", f"{env_result.average:.1f}°C")
            cols[1].metric("Hold environment range", f"{env_result.minimum:.1f}–{env_result.maximum:.1f}°C")
            cols[2].metric("Hold environment duration", f"{duration_hours(env_result.timeline):.2f} h")
            cols[3].metric("Hold stability", f"{env_result.stability_score:.0f}/100")
            delta_frames = []
            for meat_label, meat_item in meat_results.items():
                hold_meat = meat_item["valid"]
                if transfer_time is not None:
                    hold_meat = hold_meat[hold_meat["timestamp"] >= transfer_time]
                aligned = align_hold_delta(env_result.timeline, hold_meat, meat_label)
                if not aligned.empty:
                    delta_frames.append(aligned)
            if delta_frames:
                delta = pd.concat(delta_frames, ignore_index=True)
                delta_summary = delta.groupby("Profile")["Environment minus meat °C"].agg(["mean", "min", "max"]).reset_index()
                delta_summary.columns = ["Brisket profile", "Average ΔT °C", "Minimum ΔT °C", "Maximum ΔT °C"]
                delta_summary[["Average ΔT °C", "Minimum ΔT °C", "Maximum ΔT °C"]] = delta_summary[["Average ΔT °C", "Minimum ΔT °C", "Maximum ΔT °C"]].round(1)
                st.dataframe(delta_summary, hide_index=True, use_container_width=True)
                st.plotly_chart(px.line(delta, x="timestamp", y="Environment minus meat °C", color="Profile", title="Hold Environment minus internal brisket temperature"), use_container_width=True, key=f"hold_delta_{env_index}")
            st.plotly_chart(px.line(env_result.timeline, x="timestamp", y="temperature_c", title=env_label, labels={"temperature_c": "Hold Environment °C"}), use_container_width=True, key=f"hold_environment_{env_index}")

    overlay_frames = []
    for label, item in meat_results.items():
        frame = item["valid"][["timestamp", "temperature_c"]].copy(); frame.columns = ["timestamp", "Temperature °C"]; frame["Temperature source"] = label; overlay_frames.append(frame)
    for label, result in environment_results.items():
        frame = result.timeline[["timestamp", "temperature_c"]].copy(); frame.columns = ["timestamp", "Temperature °C"]; frame["Temperature source"] = label; overlay_frames.append(frame)
    overlay = pd.concat(overlay_frames, ignore_index=True).sort_values("timestamp")
    st.plotly_chart(px.line(overlay, x="timestamp", y="Temperature °C", color="Temperature source", title="Brisket, Cook Environment and Hold Environment"), use_container_width=True, key="environment_overlay")

# Detailed meat profiles retained.
st.header("Brisket probe analysis")
for index, (label, item) in enumerate(meat_results.items()):
    result, stat, sample = item["result"], stats[label], item["sample"]
    with st.expander(f"{label} — {item['source']}", expanded=len(meat_results) == 1):
        cols = st.columns(4); cols[0].metric("Session", result.detection.session_type); cols[1].metric("Confidence", f"{result.detection.confidence}%"); cols[2].metric("Detected pull", result.detection.pull_timestamp.strftime("%d/%m/%Y %H:%M") if result.detection.pull_timestamp is not None else "Not detected"); cols[3].metric("Peak", f"{result.detection.peak_temperature:.1f}°C")
        cols = st.columns(4); cols[0].metric("Cook contribution", f"{result.cook:.1%}"); cols[1].metric("Hold contribution", f"{result.hold:.1%}"); cols[2].metric("Recorded total", f"{result.total:.1%}"); cols[3].metric("Assessment", result.assessment if result.complete else "Partial session")
        cols = st.columns(4); cols[0].metric("Cook duration", f"{stat['cook_h']:.2f} h"); cols[1].metric("Hold duration", f"{stat['hold_h']:.2f} h"); cols[2].metric("Average cook", "N/A" if stat["cook_avg"] is None else f"{stat['cook_avg']:.1f}°C"); cols[3].metric("Average hold", "N/A" if stat["hold_avg"] is None else f"{stat['hold_avg']:.1f}°C")
        tabs = st.tabs(["Temperature", "Accumulated rendering", "Band calculation", "Data quality"])
        with tabs[0]:
            frame = item["valid"][["timestamp", "temperature_c"]].copy(); frame.columns = ["Timestamp", "Temperature °C"]
            st.plotly_chart(px.line(frame, x="Timestamp", y="Temperature °C", title=label), use_container_width=True, key=f"temperature_{index}")
        with tabs[1]:
            chart = px.line(result.timeline, x="Timestamp", y="Accumulated rendering"); chart.update_yaxes(tickformat=".0%")
            st.plotly_chart(chart, use_container_width=True, key=f"rendering_{index}")
        with tabs[2]:
            bands = result.summary[result.summary["Duration hours"] > 0].copy(); bands["Duration hours"] = bands["Duration hours"].round(3); bands["Rate per hour"] = bands["Rate per hour"].map(lambda value: f"{value:.1%}"); bands["Tenderness contribution"] = bands["Tenderness contribution"].map(lambda value: f"{value:.1%}")
            st.dataframe(bands, hide_index=True, use_container_width=True, key=f"bands_{index}")
        with tabs[3]:
            cols = st.columns(3); cols[0].metric("Analysed hours", f"{result.analysed_hours:.3f}"); cols[1].metric("Excluded gap hours", f"{result.excluded_gap_hours:.3f}"); cols[2].metric("Below 60°C hours", f"{result.below_model_hours:.3f}")
            st.write(f"Sampling: {sample['mode']} | Normal interval: {interval_text(sample['normal'])} | Gap threshold: {interval_text(sample['threshold'])}")

master_start = derive_master_start(meat_results, environment_results)

pdf_bytes = build_pdf_report(
    app_version=APP_VERSION,
    configuration=config,
    meat_results=meat_results,
    stats=stats,
    environment_results=environment_results,
    environment_sources=environment_sources,
    transfer_time=transfer_time,
    master_start=master_start,
    cook_aggregate=cook_aggregate,
    hold_aggregate=hold_aggregate,
)
report_slot.download_button(
    "Download full PDF report",
    data=pdf_bytes,
    file_name="brisket_session_analysis_full_report.pdf",
    mime="application/pdf",
    use_container_width=True,
)

st.caption("Cook Environment streams are evaluated before the derived transfer boundary. Hold Environment streams are evaluated after it. No environmental values are interpolated across the smoker-to-hold transfer.")
