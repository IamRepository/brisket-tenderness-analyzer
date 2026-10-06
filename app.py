from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from io import BytesIO
import re
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

import brisket_engine as engine
import pit_engine as pit
from pdf_report import build_pdf_report

APP_NAME = "Brisket Tenderness Analyzer"
APP_VERSION = "2.8.3"
POINT = pit.POINT
FLAT = pit.FLAT
COOK_PID = pit.COOK_PID
COOK_GRATE = pit.COOK_GRATE
HOLD_ENV = pit.HOLD_ENV
IGNORE = pit.IGNORE
MEAT_ROLES = {POINT, FLAT}
# One colour per entity, used by every chart (categorical order validated for colour-blind separation).
SERIES_COLOURS = {
    "point": "#2a78d6",   # Brisket - Point
    "flat": "#eb6834",    # Brisket - Flat
    "pid": "#1baf7a",     # Cook Environment - PID
    "grate": "#eda100",   # Cook Environment - Grate
    "hold": "#e87ba4",    # Hold Environment
}
PHASE_COLOURS = {"Cook": "#e34948", "Hold": "#4a3aa7"}
MARKER_COLOUR = "#52514e"
SPREAD_WARNING = 0.25  # Point/Flat totals this far apart (25 points) get a note even within one assessment band
ENVIRONMENT_ROLES = {COOK_PID, COOK_GRATE, HOLD_ENV}

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", page_icon="🔥", layout="wide")
st.title(f"🔥 {APP_NAME} {APP_VERSION}")
st.caption("Independent implementation based on brisket time-temperature and hot-hold concepts shared by Steve Gow. Results are analytical estimates.")

# Filled with the PDF download button once an analysis has run.
report_slot = st.empty()

# The sampling settings are drawn under the session summary, but the analysis
# needs their values first, so read them from the previous run's widget state.
override_gap = st.session_state.get("override_gap", False)
manual_gap = st.session_state.get("manual_gap_seconds", 10.0) if override_gap else None

TABLE_CSS = """
<style>
.bta-wrap {width:100%; overflow-x:auto; margin:0.25rem 0 1rem 0;}
.bta-table {width:100%; border-collapse:collapse; table-layout:auto; font-size:0.85rem;}
.bta-table th, .bta-table td {border:1px solid rgba(128,128,128,0.25); padding:6px 8px; vertical-align:middle; word-break:normal; overflow-wrap:normal;}
.bta-table th {background:rgba(128,128,128,0.10); font-weight:600; text-align:left;}
.bta-table.centred th, .bta-table.centred td {text-align:center;}
.bta-table td.num {text-align:right; white-space:nowrap;}
.bta-cards {display:flex; gap:1rem; flex-wrap:wrap; margin:0.25rem 0 0.5rem 0;}
.bta-card {flex:1 1 200px; border:1px solid rgba(128,128,128,0.25); border-radius:10px; padding:12px 16px;}
.bta-card .label {font-size:0.85rem; opacity:0.75;}
.bta-card .value {font-size:1.6rem; font-weight:600; line-height:1.25; margin-top:4px;}
.bta-card .value.text {font-size:1.15rem; overflow-wrap:break-word;}
</style>
"""
st.markdown(TABLE_CSS, unsafe_allow_html=True)


def html_table(rows, centred=False, numeric=()):
    """A table that wraps its text to fit the page width instead of scrolling sideways."""
    if not rows:
        return
    columns = list(rows[0].keys())
    head = "".join(f"<th>{escape(str(c))}</th>" for c in columns)
    body = ""
    for row in rows:
        cells = ""
        for c in columns:
            value = row[c]
            text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
            css = ' class="num"' if c in numeric and not centred else ""
            cells += f"<td{css}>{escape(text)}</td>"
        body += f"<tr>{cells}</tr>"
    css_class = "bta-table centred" if centred else "bta-table"
    st.markdown(f'<div class="bta-wrap"><table class="{css_class}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>', unsafe_allow_html=True)


def stat_cards(cards):
    """Metric-style cards whose text wraps instead of being cut off. cards: (label, value, is_text)."""
    html = "".join(
        f'<div class="bta-card"><div class="label">{escape(label)}</div>'
        f'<div class="value{" text" if is_text else ""}">{escape(str(value))}</div></div>'
        for label, value, is_text in cards
    )
    st.markdown(f'<div class="bta-cards">{html}</div>', unsafe_allow_html=True)


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
    text = str(value)
    # Correct the logger's spelling slips only as whole words, so "Point" stays "Point".
    text = re.sub(r" / Poin\b", " / Point", text)
    text = re.sub(r" / Enviroment\b", " / Environment", text)
    return text


def stage_display_label(label, result):
    role = str(label).split(" — ", 1)[0].replace("🥩 ", "")
    session = result.detection.session_type
    if session == "Cook + Hold":
        stage = "Cook + Hold"
    elif session in ("Hold Only", "Calibration / Hold Test"):
        stage = "Hold only"
    elif session == "Cook Only":
        stage = "Cook only"
    else:
        stage = session
    return f"{role} ({stage})"


def sampling_info(valid, override=None):
    gaps = valid["timestamp"].sort_values().diff().dt.total_seconds().dropna()
    gaps = gaps[gaps > 0]
    normal = float(gaps.median()) if not gaps.empty else None
    automatic = max(normal * 3.0, normal + 1.0) if normal is not None else 10.0
    return {"normal": normal, "threshold": float(override) if override is not None else automatic, "mode": "Manual override" if override is not None else "Automatic"}


def unique_label(role, file_label, column, existing):
    base = f"{role} — {column}"
    return base if base not in existing else f"{role} — {file_label} / {column}"


def phase_stats(valid, detection, transfer_time=None):
    """Cook and hold hours and averages, split where rendering is split.

    Rendering uses the shared transfer time, so the hours do too. A profile's
    own detected pull is only used when no transfer time could be derived.
    """
    work = valid.sort_values("timestamp").copy()
    work["elapsed"] = (work["timestamp"].shift(-1) - work["timestamp"]).dt.total_seconds()
    work = work[work["elapsed"].notna() & (work["elapsed"] > 0)]
    pull = transfer_time if transfer_time is not None else detection.pull_timestamp
    if detection.session_type == "Cook Only":
        cook, hold = work, work.iloc[0:0]
    elif detection.session_type in ("Hold Only", "Calibration / Hold Test"):
        cook, hold = work.iloc[0:0], work
    elif pull is not None:
        # Split each interval at the transfer, as the rendering calculation does.
        pull = pd.Timestamp(pull)
        cook_seconds = (pull - work["timestamp"]).dt.total_seconds().clip(lower=0)
        cook_seconds = cook_seconds.where(cook_seconds < work["elapsed"], work["elapsed"])
        cook = work.assign(elapsed=cook_seconds)
        hold = work.assign(elapsed=work["elapsed"] - cook_seconds)
        cook, hold = cook[cook["elapsed"] > 0], hold[hold["elapsed"] > 0]
    else:
        cook, hold = work.iloc[0:0], work.iloc[0:0]

    def weighted(frame):
        if frame.empty or frame["elapsed"].sum() <= 0:
            return None
        return float((frame["temperature_c"] * frame["elapsed"]).sum() / frame["elapsed"].sum())

    return {"cook_h": float(cook["elapsed"].sum()) / 3600, "hold_h": float(hold["elapsed"].sum()) / 3600, "cook_avg": weighted(cook), "hold_avg": weighted(hold)}


def derive_transfer_time(meat_results):
    pulls = sorted(
        pd.Timestamp(item["result"].detection.pull_timestamp)
        for item in meat_results.values()
        if item["result"].detection.session_type == "Cook + Hold"
        and item["result"].detection.pull_timestamp is not None
    )
    if not pulls:
        return None
    middle = len(pulls) // 2
    return pulls[middle] if len(pulls) % 2 else pulls[middle - 1] + (pulls[middle] - pulls[middle - 1]) / 2


def derive_master_start(meat_results, environment_results):
    starts = [pd.Timestamp(item["valid"]["timestamp"].min()) for item in meat_results.values() if not item["valid"].empty]
    starts.extend(pd.Timestamp(result.timeline["timestamp"].min()) for result in environment_results.values() if not result.timeline.empty)
    return min(starts) if starts else None


def elapsed_frame(frame, master_start, time_col="timestamp", value_col="temperature_c"):
    output = frame[[time_col, value_col]].copy()
    output["Elapsed session hours"] = (pd.to_datetime(output[time_col]) - pd.Timestamp(master_start)).dt.total_seconds() / 3600
    return output


def add_transfer_marker(figure, transfer_time, master_start):
    if transfer_time is None or master_start is None:
        return figure
    x = (pd.Timestamp(transfer_time) - pd.Timestamp(master_start)).total_seconds() / 3600
    figure.add_vline(x=x, line_width=1.5, line_dash="dash", line_color=MARKER_COLOUR)
    figure.add_annotation(x=x, y=1, yref="paper", yanchor="bottom", text="Smoker to hold", showarrow=False, xanchor="left", xshift=4, font={"color": MARKER_COLOUR, "size": 12})
    return figure


def segment_environment(prepared, role, transfer_time):
    if transfer_time is None:
        return prepared.copy()
    if role in {COOK_PID, COOK_GRATE}:
        return prepared[prepared["timestamp"] < transfer_time].copy()
    if role == HOLD_ENV:
        return prepared[prepared["timestamp"] >= transfer_time].copy()
    return prepared.copy()


def duration_hours(frame):
    if frame is None or len(frame) < 2:
        return 0.0
    return max((frame["timestamp"].iloc[-1] - frame["timestamp"].iloc[0]).total_seconds() / 3600, 0.0)


def plain_role(label):
    """'🥩 Brisket - Point — Point' -> 'Brisket - Point'."""
    text = str(label).split(" — ", 1)[0]
    for icon in ("🥩", "🔥", "🌡", "♨", "🚫"):
        text = text.replace(icon, "")
    return text.strip()


def series_colour(name):
    """One fixed colour per entity in every chart, so Point is always the same blue."""
    text = str(name).lower()
    for key, colour in SERIES_COLOURS.items():
        if key in text:
            return colour
    return "#52514e"


def break_gaps(frame, max_gap_seconds, x_col="Elapsed session hours"):
    """Insert an empty point inside every recording gap so chart lines stop there instead of bridging it."""
    if frame.empty or max_gap_seconds is None:
        return frame
    frame = frame.sort_values(x_col).reset_index(drop=True)
    gaps = frame[x_col].diff() * 3600 > max_gap_seconds
    if not gaps.any():
        return frame
    breaks = frame.loc[gaps, [x_col]].copy()
    breaks[x_col] = breaks[x_col] - 1e-6
    return pd.concat([frame, breaks], ignore_index=True).sort_values(x_col, kind="stable").reset_index(drop=True)


def style_figure(figure, unit, hover="x unified"):
    """Shared look: recessive grid, legend above the plot, readable hover."""
    figure.update_layout(
        hovermode=hover,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0, "title": None},
        title={"x": 0, "xanchor": "left", "y": 0.98, "yanchor": "top", "pad": {"b": 30}},
        margin={"t": 96, "l": 8, "r": 8, "b": 8},
    )
    figure.update_traces(line={"width": 2}, selector={"type": "scatter"})
    figure.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)", zeroline=False, automargin=True)
    figure.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)", zeroline=False, automargin=True, ticksuffix=f" {unit}" if unit == "%" else "")
    return figure


def accumulated_frame(result, master_start):
    """Accumulated rendering (%) at the end of every interval, starting from 0 %. Gaps stay empty."""
    timeline = result.timeline
    if timeline.empty:
        return pd.DataFrame(columns=["Elapsed session hours", "Accumulated %"])
    start = pd.Timestamp(master_start)
    points = [{"Elapsed session hours": (pd.Timestamp(timeline["Timestamp"].iloc[0]) - start).total_seconds() / 3600, "Accumulated %": 0.0}]
    for _, row in timeline.iterrows():
        end_hours = (pd.Timestamp(row["Next timestamp"]) - start).total_seconds() / 3600
        if str(row["Status"]).startswith("Excluded"):
            points.append({"Elapsed session hours": end_hours - 1e-6, "Accumulated %": None})
        points.append({"Elapsed session hours": end_hours, "Accumulated %": row["Accumulated rendering"] * 100})
    return pd.DataFrame(points)


def crossing_time(result, target):
    """When accumulated rendering first reached target (a fraction), interpolated within the interval."""
    previous = 0.0
    for _, row in result.timeline.iterrows():
        current = row["Accumulated rendering"]
        if current >= target and row["Incremental rendering"] > 0:
            share = (target - previous) / row["Incremental rendering"]
            return pd.Timestamp(row["Timestamp"]) + pd.Timedelta(seconds=row["Elapsed seconds"] * share)
        previous = current
    return None


def milestone_text(result, master_start, transfer_time):
    """One line: when the ideal range was entered and left, and in which phase."""
    def when(moment):
        hours = (moment - pd.Timestamp(master_start)).total_seconds() / 3600
        phase = ""
        if transfer_time is not None:
            phase = " during the cook" if moment < pd.Timestamp(transfer_time) else " during the hold"
        return f"{hours:.1f} h ({moment:%d/%m %H:%M}){phase}"
    entered, left = crossing_time(result, 0.95), crossing_time(result, 1.05)
    final = f"ended at {result.total:.1%}"
    if entered is None:
        return f"did not reach the ideal range; {final}."
    if left is None:
        return f"reached the ideal range (95 %) at {when(entered)} and {final}."
    return f"reached the ideal range (95 %) at {when(entered)}, passed 105 % at {when(left)}, and {final}."


def band_frame(result):
    """Rendering contribution per temperature band and phase, for the band breakdown chart."""
    summary = result.summary[result.summary["Duration hours"] > 0].copy()
    if summary.empty:
        return summary
    summary["Phase"] = summary["Phase"].replace({"Hold / cooldown": "Hold"})
    summary["Contribution %"] = summary["Tenderness contribution"] * 100
    summary["Temperature range"] = summary["Temperature range"].str.replace("°C", " °C", regex=False)
    return summary.sort_values(["Band", "Phase"])


def environment_integrity_rows(environment_results):
    """Summarise whether the expected environment roles are available."""
    rows = []
    for role in (COOK_PID, COOK_GRATE, HOLD_ENV):
        matches = [label for label, result in environment_results.items() if result.role == role]
        rows.append({
            "Environment role": role,
            "Status": "Present" if matches else "Missing",
            "Streams": len(matches),
        })
    return rows


def build_composite_environment(environment_results):
    """Build stage curves without blending Cook and Hold or bridging transfer gaps.

    Cook Grate is preferred over Cook PID. If Cook Grate is unavailable, Cook
    PID is used. Multiple Hold Environment streams are aligned and averaged only
    where readings overlap within timestamp tolerance.
    """
    cook_grate = [(k, v) for k, v in environment_results.items() if v.role == COOK_GRATE]
    cook_pid = [(k, v) for k, v in environment_results.items() if v.role == COOK_PID]
    hold = [(k, v) for k, v in environment_results.items() if v.role == HOLD_ENV]

    def one_stream(candidates, phase):
        if not candidates:
            return pd.DataFrame()
        frame = candidates[0][1].timeline[["timestamp", "temperature_c"]].copy()
        frame.rename(columns={"temperature_c": "Environment aggregate °C"}, inplace=True)
        frame["Available sensors"] = 1
        frame["Phase"] = phase
        return frame

    cook = one_stream(cook_grate or cook_pid, "Cook")

    if not hold:
        hold_frame = pd.DataFrame()
    elif len(hold) == 1:
        hold_frame = one_stream(hold, "Hold")
    else:
        renamed = []
        for index, (_, result) in enumerate(hold):
            frame = result.timeline[["timestamp", "temperature_c"]].copy()
            frame.rename(columns={"temperature_c": f"sensor_{index}"}, inplace=True)
            renamed.append(frame.set_index("timestamp"))
        joined = pd.concat(renamed, axis=1).sort_index()
        hold_frame = joined.mean(axis=1, skipna=True).rename("Environment aggregate °C").to_frame()
        hold_frame["Available sensors"] = joined.notna().sum(axis=1)
        hold_frame["Phase"] = "Hold"
        hold_frame.reset_index(inplace=True)

    return cook, hold_frame


def consolidate_meat_profiles(raw_results, manual_gap=None):
    """Create one continuous profile per physical brisket location.

    Point streams are combined only with Point streams, and Flat streams only
    with Flat streams. Probes are averaged on a shared time grid even when they
    sample at different seconds; each probe holds its latest reading only up to
    its own gap threshold, so genuine gaps are not bridged.
    """
    consolidated = {}
    for role in (POINT, FLAT):
        members = [
            (label, item) for label, item in raw_results.items()
            if label.split(" — ", 1)[0] == role
        ]
        if not members:
            continue

        streams = []
        sources = []
        source_rows = valid_rows = invalid_rows = duplicate_timestamps = 0
        for _, item in members:
            streams.append((item["valid"], item["sample"]["threshold"]))
            sources.append(item["source"])
            report = item["result"].report
            source_rows += report.source_rows
            valid_rows += report.valid_rows
            invalid_rows += report.invalid_rows
            duplicate_timestamps += report.duplicate_timestamps

        canonical = engine.combine_probes(streams)
        if len(canonical) < 5:
            continue
        valid = canonical[["timestamp", "temperature_c"]].copy()
        report = engine.ParseReport(
            source_rows=source_rows,
            valid_rows=len(valid),
            invalid_rows=invalid_rows,
            duplicate_timestamps=duplicate_timestamps,
            first_timestamp=valid["timestamp"].min(),
            last_timestamp=valid["timestamp"].max(),
        )
        detection = engine.classify_session(valid)
        # The combined grid can hold readings a second apart, so its own median
        # spacing says nothing about real gaps. Use the member probes' settings.
        member_samples = [item["sample"] for _, item in members]
        normals = [x["normal"] for x in member_samples if x["normal"] is not None]
        sample = {
            "normal": max(normals) if normals else None,
            "threshold": max(x["threshold"] for x in member_samples),
            "mode": member_samples[0]["mode"],
        }
        result = engine.analyse(valid, report, detection, max_gap=sample["threshold"])
        consolidated[role] = {
            "result": result,
            "valid": valid,
            "source": "; ".join(dict.fromkeys(sources)),
            "sources": list(dict.fromkeys(sources)),
            "sample": sample,
            "contributing_probes": canonical[["timestamp", "contributing_probes"]],
        }
    return consolidated


def reanalyse_canonical_profiles(canonical_results, transfer_time):
    """Calculate rendering once per physical location over Cook plus Hold."""
    updated = {}
    for role, item in canonical_results.items():
        result = engine.analyse(
            item["valid"],
            item["result"].report,
            item["result"].detection,
            pull_override=transfer_time,
            max_gap=item["sample"]["threshold"],
        )
        updated[role] = {**item, "result": result}
    return updated


def overall_brisket_assessment(canonical_results):
    """Return one whole-brisket assessment from canonical Point and Flat totals."""
    totals = [item["result"].total for item in canonical_results.values()]
    if not totals:
        return None
    overall_total = float(sum(totals) / len(totals))
    point_total = canonical_results[POINT]["result"].total if POINT in canonical_results else None
    flat_total = canonical_results[FLAT]["result"].total if FLAT in canonical_results else None
    spread_note = None
    if point_total is not None and flat_total is not None:
        point_text, flat_text = engine.assess(point_total), engine.assess(flat_total)
        gap = abs(point_total - flat_total)
        if point_text != flat_text or gap >= SPREAD_WARNING:
            spread_note = (
                f"Point and Flat differ by {gap * 100:.0f} percentage points "
                f"(Point {point_total:.1%}, {point_text.lower()}; Flat {flat_total:.1%}, {flat_text.lower()}). "
                "The overall figure is their average and describes neither end on its own; read them separately."
            )
    return {
        "total": overall_total,
        "assessment": engine.assess(overall_total),
        "locations": len(totals),
        "point_total": point_total,
        "flat_total": flat_total,
        "spread_note": spread_note,
    }

# Step 1
st.header("Step 1: Upload temperature files")
upload_left, upload_right = st.columns(2)
primary = upload_left.file_uploader("Primary temperature file", type=["xlsx", "xlsm", "xls", "csv"], key="primary")
secondary = upload_right.file_uploader("Secondary temperature file (optional)", type=["xlsx", "xlsm", "xls", "csv"], key="secondary")
# Results stay on screen once analysed; a new set of files starts over.
files_key = (primary.file_id if primary else None, secondary.file_id if secondary else None)
if st.session_state.get("analysed_files") != files_key:
    st.session_state.analysed = False
if primary is None:
    st.info("Upload a primary temperature file to begin.")
    st.stop()
try:
    primary_name = primary.name
    sheets = load_file(primary.getvalue(), primary.name)
    primary_sheet = upload_left.selectbox("Primary worksheet", list(sheets), key="primary_sheet")
    primary_df = sheets[primary_sheet]
    primary_time, _ = engine.detect_columns(primary_df)
    classifications = pit.classify_columns(primary_df, primary_time)
    classifications["File"] = "Primary"
    secondary_name = secondary_df = secondary_time = None
    if secondary is not None:
        secondary_name = secondary.name
        sheets2 = load_file(secondary.getvalue(), secondary.name)
        secondary_sheet = upload_right.selectbox("Secondary worksheet", list(sheets2), key="secondary_sheet")
        secondary_df = sheets2[secondary_sheet]
        secondary_time, _ = engine.detect_columns(secondary_df)
        classifications2 = pit.classify_columns(secondary_df, secondary_time)
        classifications2["File"] = "Secondary"
        classifications = pd.concat([classifications, classifications2], ignore_index=True)
except Exception as exc:
    st.error(f"File setup failed: {exc}")
    st.stop()

# Step 2
st.header("Step 2: Assign probe and environment roles")
roles = {}
role_columns = dict(zip(("Primary", "Secondary"), st.columns(2)))
for file_label, colour, icon in (("Primary", "#eaf3ff", "🟦"), ("Secondary", "#edf9ef", "🟩")):
    group = classifications[classifications["File"] == file_label]
    if group.empty:
        continue
    filename = primary_name if file_label == "Primary" else secondary_name
    with role_columns[file_label]:
        st.markdown(f'<div style="background:{colour};color:#172033;padding:12px 16px;border-radius:10px;border:1px solid #d8dee6;margin-bottom:8px"><b>{icon} {file_label.upper()} FILE</b><br><small>{escape(str(filename))}</small></div>', unsafe_allow_html=True)
        for _, row in group.iterrows():
            raw_column = row["Column"]
            shown_column = row.get("Display column", pit.normalise_column_name(raw_column))
            key = f"{file_label}_{raw_column}"
            suggestion = row["Suggested role"]
            index = pit.ROLES.index(suggestion) if suggestion in pit.ROLES else pit.ROLES.index(IGNORE)
            roles[key] = st.selectbox(f"{shown_column} ({row['Confidence']}% suggested confidence)", pit.ROLES, index=index, key=f"role_{key}")

config = []
for _, row in classifications.iterrows():
    role = roles[f"{row['File']}_{row['Column']}"]
    if role != IGNORE:
        shown = row.get("Display column", pit.normalise_column_name(row["Column"]))
        file_name = primary_name if row["File"] == "Primary" else secondary_name
        config.append({"Role": role, "Source": display_source(f"{row['File']} / {shown}"), "File name": file_name})
st.subheader("Detected configuration")
html_table(config)
if st.button("Analyse session", type="primary", width="stretch"):
    st.session_state.analysed = True
    st.session_state.analysed_files = files_key
if not st.session_state.get("analysed"):
    st.stop()

raw_meat_results, environment_inputs, errors = {}, [], []
for _, row in classifications.iterrows():
    file_label, raw_column = row["File"], row["Column"]
    shown_column = row.get("Display column", pit.normalise_column_name(raw_column))
    role = roles[f"{file_label}_{raw_column}"]
    if role == IGNORE:
        continue
    active_df = primary_df if file_label == "Primary" else secondary_df
    active_time = primary_time if file_label == "Primary" else secondary_time
    try:
        if role in MEAT_ROLES:
            valid, report = engine.prepare(active_df, active_time, raw_column)
            detection = engine.classify_session(valid)
            sample = sampling_info(valid, manual_gap)
            result = engine.analyse(valid, report, detection, max_gap=sample["threshold"])
            label = unique_label(role, file_label, shown_column, raw_meat_results)
            raw_meat_results[label] = {"result": result, "valid": valid, "source": display_source(f"{file_label} / {shown_column}"), "sample": sample}
        elif role in ENVIRONMENT_ROLES:
            environment_inputs.append({"role": role, "file": file_label, "column": raw_column, "shown": shown_column, "df": active_df, "time": active_time})
    except Exception as exc:
        errors.append(f"{file_label} / {shown_column}: {exc}")
for error in errors:
    st.error(error)
if not raw_meat_results:
    st.error("At least one column must be classified as Brisket - Point or Brisket - Flat.")
    st.stop()

meat_results = consolidate_meat_profiles(raw_meat_results, manual_gap)
transfer_time = derive_transfer_time(meat_results)
meat_results = reanalyse_canonical_profiles(meat_results, transfer_time)
overall_assessment = overall_brisket_assessment(meat_results)
environment_results, environment_sources = {}, {}
for item in environment_inputs:
    prepared = pit.prepare(item["df"], item["time"], item["column"])
    segmented = segment_environment(prepared, item["role"], transfer_time)
    if segmented.empty:
        st.warning(f"{item['file']} / {item['shown']}: no readings overlap the applicable phase.")
        continue
    label = f"{item['role']} — {item['shown']}"
    environment_results[label] = pit.analyse(segmented, item["role"])
    environment_sources[label] = display_source(f"{item['file']} / {item['shown']}")

master_start = derive_master_start(meat_results, environment_results)
st.header("Session detection summary")
if transfer_time is not None:
    st.info(f"Derived smoker-to-hold transfer boundary: {transfer_time:%d/%m/%Y %H:%M:%S}")
summary, stats = [], {}
for label, item in meat_results.items():
    result = item["result"]
    stat = phase_stats(item["valid"], result.detection, transfer_time)
    stats[label] = stat
    pull = result.detection.pull_timestamp
    one_decimal = lambda v: "" if v is None else f"{v:.1f}"
    summary.append({
        "Profile": stage_display_label(label, result),
        "Source": item["source"],
        "Session type": result.detection.session_type,
        "Conf.": f"{result.detection.confidence}%",
        "Detected pull": "" if pull is None else f"{pull:%d/%m/%Y %H:%M}",
        "Peak °C": one_decimal(result.detection.peak_temperature),
        "Avg °C": one_decimal(result.detection.average_temperature),
        "Min °C": one_decimal(result.detection.minimum_temperature),
        "Cook h": f"{stat['cook_h']:.2f}",
        "Hold h": f"{stat['hold_h']:.2f}",
        "Avg cook °C": one_decimal(stat["cook_avg"]),
        "Avg hold °C": one_decimal(stat["hold_avg"]),
        "Sampling": interval_text(item["sample"]["normal"]),
        "Gap limit": interval_text(item["sample"]["threshold"]),
    })
html_table(summary, numeric=("Conf.", "Peak °C", "Avg °C", "Min °C", "Cook h", "Hold h", "Avg cook °C", "Avg hold °C"))

with st.expander("Advanced sampling settings"):
    st.checkbox("Override automatic gap detection", value=False, key="override_gap")
    if st.session_state.get("override_gap"):
        st.number_input("Maximum accepted gap (seconds)", 1.0, 86400.0, 10.0, 1.0, key="manual_gap_seconds")
    st.caption("Intervals longer than the gap limit are treated as recording gaps and left out of the rendering calculation. Changes apply immediately.")

st.header("Environment integrity")
integrity = environment_integrity_rows(environment_results)
html_table(integrity, centred=True)

if overall_assessment is not None:
    st.header("Whole brisket tenderness assessment")
    stat_cards([
        ("Overall rendering", f"{overall_assessment['total']:.1%}", False),
        ("Assessment", overall_assessment["assessment"], True),
        ("Canonical locations", overall_assessment["locations"], False),
    ])
    if overall_assessment["spread_note"]:
        st.warning(overall_assessment["spread_note"])
    st.caption("Overall rendering is the mean of the canonical Point and Flat totals. Probes at the same location are averaged over time before rendering is calculated.")

st.header("Brisket probe comparison")
rows, frames = [], []
for label, item in meat_results.items():
    result = item["result"]
    display_label = stage_display_label(label, result)
    rows.append({"Profile": display_label, "Source": item["source"], "Cook": f"{result.cook:.1%}", "Hold": f"{result.hold:.1%}", "Total": f"{result.total:.1%}", "Assessment": result.assessment if result.complete else "Partial session", "Analysed h (≥ 60 °C)": f"{result.analysed_hours:.2f}"})
    frame = break_gaps(elapsed_frame(item["valid"], master_start), item["sample"]["threshold"])
    frame["Profile"] = display_label
    frames.append(frame)
html_table(rows, numeric=("Cook", "Hold", "Total", "Analysed h (≥ 60 °C)"))
combined = pd.concat(frames, ignore_index=True)
probe_figure = px.line(
    combined, x="Elapsed session hours", y="temperature_c", color="Profile",
    color_discrete_map={name: series_colour(name) for name in combined["Profile"].unique()},
    title="Brisket Point and Flat temperature profiles",
    labels={"Elapsed session hours": "Elapsed time from session start (hours)", "temperature_c": "Temperature (°C)", "Profile": ""},
)
probe_figure = style_figure(add_transfer_marker(probe_figure, transfer_time, master_start), "°C")
st.plotly_chart(probe_figure, width="stretch", key="brisket_comparison")

if environment_results:
    st.header("Environment analysis")
    env_rows, env_frames = [], []
    for label, result in environment_results.items():
        stage = "Hold" if result.role == HOLD_ENV else "Cook"
        stream_name = plain_role(label)
        env_rows.append({"Environment stream": stream_name, "Source": environment_sources[label], "Stage": stage, "Start": f"{result.timeline['timestamp'].min():%d/%m %H:%M}", "End": f"{result.timeline['timestamp'].max():%d/%m %H:%M}", "Hours": f"{duration_hours(result.timeline):.2f}", "Avg °C": f"{result.average:.1f}", "Min °C": f"{result.minimum:.1f}", "Max °C": f"{result.maximum:.1f}", "Stability": f"{result.stability_score:.0f}/100", "Level changes": result.level_changes})
        frame = break_gaps(elapsed_frame(result.timeline, master_start), sampling_info(result.timeline)["threshold"])
        frame["Environment stream"] = stream_name
        env_frames.append(frame)
    html_table(env_rows, numeric=("Hours", "Avg °C", "Min °C", "Max °C", "Level changes"))
    st.caption("Stability (0–100) is scored while the cooker holds a temperature; planned setpoint steps and the ramps between them are not counted against it.")
    env_combined = pd.concat(env_frames, ignore_index=True)
    env_figure = px.line(
        env_combined, x="Elapsed session hours", y="temperature_c", color="Environment stream",
        color_discrete_map={name: series_colour(name) for name in env_combined["Environment stream"].unique()},
        title="Cook and Hold Environment profiles",
        labels={"Elapsed session hours": "Elapsed time from session start (hours)", "temperature_c": "Temperature (°C)", "Environment stream": ""},
    )
    env_figure = style_figure(add_transfer_marker(env_figure, transfer_time, master_start), "°C")
    st.plotly_chart(env_figure, width="stretch", key="environment_profiles")

st.header("Rendering by location")
st.caption("How rendering built up over the session, and which temperature bands it came from. The green band is the ideal range (95–105 %).")
accumulated = []
for label, item in meat_results.items():
    frame = accumulated_frame(item["result"], master_start)
    frame["Location"] = stage_display_label(label, item["result"])
    accumulated.append(frame)
accumulated = pd.concat(accumulated, ignore_index=True)
rendering_figure = px.line(
    accumulated, x="Elapsed session hours", y="Accumulated %", color="Location",
    color_discrete_map={name: series_colour(name) for name in accumulated["Location"].unique()},
    title="Accumulated rendering over time",
    labels={"Elapsed session hours": "Elapsed time from session start (hours)", "Accumulated %": "Accumulated rendering (%)", "Location": ""},
)
rendering_figure.add_hrect(y0=95, y1=105, fillcolor="rgba(12,163,12,0.14)", line_width=0, layer="below",
                           annotation_text="Ideal", annotation_position="top left", annotation_font_color="#0a7a0a")
rendering_figure = style_figure(add_transfer_marker(rendering_figure, transfer_time, master_start), "%")
st.plotly_chart(rendering_figure, width="stretch", key="accumulated_rendering")
for label, item in meat_results.items():
    st.markdown(f"**{plain_role(label)}:** {milestone_text(item['result'], master_start, transfer_time)}")

band_columns = st.columns(len(meat_results))
for column, (label, item) in zip(band_columns, meat_results.items()):
    bands = band_frame(item["result"])
    with column:
        if bands.empty:
            st.info(f"{plain_role(label)}: no time at or above 60 °C.")
            continue
        band_figure = px.bar(
            bands, x="Temperature range", y="Contribution %", color="Phase", barmode="stack",
            color_discrete_map=PHASE_COLOURS, title=f"Band breakdown – {plain_role(label)}",
            category_orders={"Temperature range": list(dict.fromkeys(bands["Temperature range"])), "Phase": list(PHASE_COLOURS)},
            custom_data=["Duration hours", "Rate per hour"],
            labels={"Contribution %": "Rendering (%)", "Temperature range": "", "Phase": ""},
        )
        band_figure.update_traces(
            hovertemplate="%{x}<br>%{fullData.name}: %{y:.1f} %<br>%{customdata[0]:.2f} h at %{customdata[1]:.1%} per hour<extra></extra>",
            marker_line_width=0,
        )
        band_figure = style_figure(band_figure, "%", hover="closest")
        band_figure.update_layout(bargap=0.25, xaxis_tickangle=-35)
        st.plotly_chart(band_figure, width="stretch", key=f"bands_{label}")

def report_time():
    """Now in the viewer's time zone (from the browser); UTC if it is unknown."""
    try:
        zone = st.context.timezone
        if zone:
            return datetime.now(ZoneInfo(zone))
    except Exception:
        pass
    return datetime.now(timezone.utc)


cook_aggregate, hold_aggregate = build_composite_environment(environment_results)
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
    environment_integrity=integrity,
    overall_assessment=overall_assessment,
    generated_at=report_time(),
)
report_name = f"brisket_tenderness_report_{pd.Timestamp(master_start):%Y-%m-%d}.pdf" if master_start is not None else "brisket_tenderness_report.pdf"
report_slot.download_button("📄 Download full PDF report", data=pdf_bytes, file_name=report_name, mime="application/pdf", type="primary", width="stretch", on_click="ignore")
st.caption("All charts use one master session timeline. Cook Environment streams are evaluated before the transfer boundary, Hold Environment streams after it, and missing values are not interpolated across transfer.")
