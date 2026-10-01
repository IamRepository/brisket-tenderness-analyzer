from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import brisket_engine as meat

POINT = "🥩 Brisket - Point"
FLAT = "🥩 Brisket - Flat"
COOK_PID = "🔥 Cook Environment - PID"
COOK_GRATE = "🌡 Cook Environment - Grate"
HOLD_ENV = "♨ Hold Environment - Probe"
IGNORE = "🚫 Ignore"

ROLES = [POINT, FLAT, COOK_PID, COOK_GRATE, HOLD_ENV, IGNORE]


@dataclass(frozen=True)
class PitResult:
    role: str
    timeline: pd.DataFrame
    average: float
    minimum: float
    maximum: float
    stddev: float
    stability_score: float
    lid_events: pd.DataFrame


def normalise_column_name(value: str) -> str:
    """Normalise common source spelling without modifying the uploaded file."""
    text = str(value).strip()
    lowered = text.lower()
    if lowered == "poin":
        return "Point"
    if lowered == "enviroment":
        return "Environment"
    return text


def classify_columns(df: pd.DataFrame, timestamp_col: str) -> pd.DataFrame:
    """Suggest conservative semantic roles for usable temperature columns."""
    rows = []

    for column in df.columns:
        if column == timestamp_col:
            continue

        values = meat.parse_temp(df[column])
        valid = values.dropna()
        if len(valid) < 2:
            continue

        display_column = normalise_column_name(column)
        name = display_column.lower()

        if "point" in name:
            role, confidence = POINT, 95
        elif "flat" in name:
            role, confidence = FLAT, 95
        elif any(hint in name for hint in (
            "hold environment", "hold probe", "holding probe",
            "holding oven", "oven probe", "hold oven", "cvap",
            "holding cabinet", "environment",
        )):
            role, confidence = HOLD_ENV, 94
        elif any(hint in name for hint in (
            "grate", "ambient", "pit probe", "chamber probe",
        )):
            role, confidence = COOK_GRATE, 93
        elif any(hint in name for hint in (
            "cavity temperature", "controller", "smoker", "smoque",
            "built-in pit", "pid",
        )):
            role, confidence = COOK_PID, 92
        elif any(hint in name for hint in (
            "meat", "internal", "food", "brisket", "probe",
        )):
            # Generic probe headings cannot establish Point versus Flat.
            role, confidence = FLAT, 65
        else:
            window = min(21, max(3, len(valid) // 50))
            smooth = valid.rolling(window, center=True, min_periods=1).median()
            start_window = max(2, len(smooth) // 20)
            rise = float(smooth.max() - smooth.iloc[:start_window].median())
            fluctuation = float(valid.diff().abs().median())

            if rise >= 20:
                role, confidence = FLAT, 55
            elif fluctuation >= 0.5:
                role, confidence = COOK_GRATE, 60
            else:
                role, confidence = COOK_PID, 55

        rows.append({
            "Column": column,
            "Display column": display_column,
            "Suggested role": role,
            "Confidence": confidence,
        })

    return pd.DataFrame(rows)


def prepare(df: pd.DataFrame, timestamp_col: str, temp_col: str) -> pd.DataFrame:
    prepared = pd.DataFrame({
        "timestamp": meat.parse_ts(df[timestamp_col]),
        "temperature_c": meat.parse_temp(df[temp_col]),
    }).dropna()
    prepared = prepared[prepared["temperature_c"].between(-20, 400)]
    return (
        prepared.sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )


def analyse(prepared: pd.DataFrame, role: str) -> PitResult:
    if prepared.empty:
        raise ValueError("The selected environmental profile contains no valid data.")

    values = prepared["temperature_c"].astype(float)
    stddev = float(values.std(ddof=0))
    median_change = float(values.diff().abs().median()) if len(values) > 1 else 0.0
    stability_score = float(np.clip(100 - 4 * stddev - 8 * median_change, 0, 100))

    lid_events = pd.DataFrame()
    if role == COOK_GRATE and len(prepared) > 4:
        differences = values.diff(5)
        event_mask = differences <= -12
        candidates = prepared.loc[event_mask, ["timestamp", "temperature_c"]].copy()
        if not candidates.empty:
            candidates["Drop °C"] = (-differences.loc[event_mask]).round(1).to_numpy()
            lid_events = candidates.rename(columns={
                "timestamp": "Detected time",
                "temperature_c": "Temperature °C",
            }).head(20)

    return PitResult(
        role=role,
        timeline=prepared,
        average=round(float(values.mean()), 1),
        minimum=round(float(values.min()), 1),
        maximum=round(float(values.max()), 1),
        stddev=round(stddev, 2),
        stability_score=stability_score,
        lid_events=lid_events,
    )


def align(meat_timeline: pd.DataFrame, environment_results: dict[str, PitResult]) -> pd.DataFrame:
    overlay = (
        meat_timeline[["Timestamp", "Temperature °C"]]
        .rename(columns={"Timestamp": "timestamp", "Temperature °C": "Meat temperature"})
        .sort_values("timestamp")
    )

    for label, result in environment_results.items():
        stream = result.timeline.rename(columns={"temperature_c": label}).sort_values("timestamp")
        intervals = stream["timestamp"].diff().dt.total_seconds().dropna()
        intervals = intervals[intervals > 0]
        tolerance = max(float(intervals.median()) * 2, 60.0) if not intervals.empty else 60.0
        overlay = pd.merge_asof(
            overlay.sort_values("timestamp"),
            stream[["timestamp", label]],
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta(seconds=tolerance),
        )

    return overlay
