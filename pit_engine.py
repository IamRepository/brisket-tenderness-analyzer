from __future__ import annotations

from dataclasses import dataclass
import re

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
    level_changes: int = 0
    steady_share: float = 1.0


def normalise_column_name(value: str) -> str:
    """Normalise common source spelling without modifying the uploaded file."""
    text = str(value).strip()
    lowered = text.lower()
    if lowered == "poin":
        return "Point"
    if lowered == "enviroment":
        return "Environment"
    return text


# Columns that are not temperatures at all: suggested as Ignore.
NOT_TEMPERATURE = re.compile(
    r"set\s*-?\s*point|target|soll|fan|lüfter|luefter|blower|damper|battery|batterie|akku"
    r"|humidity|feuchte|volt|rssi|signal|power|leistung|%|percent|prozent|status|event|note|comment",
    re.IGNORECASE,
)


def _word(pattern):
    """Match pattern as its own word, so 'point' does not match inside 'setpoint'."""
    return re.compile(rf"(?<![a-zäöü]){pattern}(?![a-zäöü])", re.IGNORECASE)


POINT_NAME = _word(r"point")
FLAT_NAME = _word(r"flat")
HOLD_NAMES = re.compile(r"hold|holding|cvap|cambro|warming|environment|warmhalte", re.IGNORECASE)
GRATE_NAMES = re.compile(r"grate|ambient|chamber|garraum|umgebung|rost", re.IGNORECASE)
PIT_NAME = _word(r"pit")
PID_NAMES = re.compile(r"cavity|controller|smoker|smoque|built-in pit|(?<![a-z])pid(?![a-z])|regler", re.IGNORECASE)
MEAT_NAMES = re.compile(r"meat|internal|food|brisket|probe|fühler|fuehler|kern|fleisch", re.IGNORECASE)


def classify_columns(df: pd.DataFrame, timestamp_col: str) -> pd.DataFrame:
    """Suggest conservative semantic roles for usable temperature columns.

    Names decide first, as whole words: setpoints, fan and battery readings are
    suggested as Ignore; 'Point' and 'Flat' as the brisket locations; hold,
    grate/pit/ambient and controller names as environments. Unnamed columns
    fall back to the shape of their readings.
    """
    rows = []

    for column in df.columns:
        if column == timestamp_col:
            continue

        values = meat.parse_temp(df[column])
        valid = values.dropna()
        if len(valid) < 2:
            continue

        display_column = normalise_column_name(column)
        name = display_column

        if NOT_TEMPERATURE.search(name):
            role, confidence = IGNORE, 90
        elif POINT_NAME.search(name):
            role, confidence = POINT, 95
        elif FLAT_NAME.search(name):
            role, confidence = FLAT, 95
        elif HOLD_NAMES.search(name):
            role, confidence = HOLD_ENV, 94
        elif GRATE_NAMES.search(name) or PIT_NAME.search(name):
            role, confidence = COOK_GRATE, 93
        elif PID_NAMES.search(name):
            role, confidence = COOK_PID, 92
        elif MEAT_NAMES.search(name):
            # Generic probe headings cannot establish Point versus Flat.
            role, confidence = FLAT, 65
        elif valid.max() > 150 or valid.min() < -20:
            role, confidence = IGNORE, 60
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

    return pd.DataFrame(rows, columns=["Column", "Display column", "Suggested role", "Confidence"])


def prepare(df: pd.DataFrame, timestamp_col: str, temp_col: str, tz: str = meat.LOCAL_TZ) -> pd.DataFrame:
    prepared = pd.DataFrame({
        "timestamp": meat.parse_ts(df[timestamp_col], tz),
        "temperature_c": meat.parse_temp(df[temp_col]),
    }).dropna()
    prepared = prepared[prepared["temperature_c"].between(-20, 400)]
    return (
        prepared.sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )


BASELINE_WINDOW = "60min"      # rolling median that follows deliberate setpoint steps
RAMP_SPAN = "15min"            # span over which the baseline's movement is measured
RAMP_RATE_C_PER_H = 10.0       # baseline moving faster than this = changing level, not holding
LEVEL_STEP_C = 5.0             # a held level this far from the previous one counts as a change
MIN_LEVEL_HOLD = "30min"       # a level must be held this long to count


def steady_stability(prepared: pd.DataFrame) -> tuple[float, int, float]:
    """Stability while the cooker holds a temperature, ignoring planned steps.

    The baseline is a centred one-hour rolling median of the readings, which
    follows a stepped program (e.g. 80 -> 110 -> 150 C) without smearing it.
    Readings taken while that baseline is ramping are left out; the score uses
    the remaining readings' deviation from the baseline and their reading-to-
    reading changes, on the same 0-100 scale as before.
    Returns (score, number of level changes, share of readings that were steady).
    """
    frame = prepared[["timestamp", "temperature_c"]].dropna().sort_values("timestamp")
    if len(frame) < 3:
        return 100.0, 0, 1.0
    series = pd.Series(frame["temperature_c"].astype(float).to_numpy(), index=pd.DatetimeIndex(frame["timestamp"]))
    baseline = series.rolling(BASELINE_WINDOW, center=True, min_periods=1).median()
    # How far the baseline moved in the last 15 minutes, as a rate. Measuring over
    # 15 minutes keeps sensor noise from looking like a ramp at fast sampling.
    window = baseline.rolling(RAMP_SPAN, closed="both", min_periods=1)
    moved = window.max() - window.min()
    steady = moved <= RAMP_RATE_C_PER_H * pd.Timedelta(RAMP_SPAN).total_seconds() / 3600
    if steady.sum() < 2:
        return 0.0, 0, float(steady.mean())

    residual = (series - baseline)[steady]
    changes = series.diff().abs()[steady & steady.shift(1, fill_value=False)]
    median_change = float(changes.median()) if not changes.empty else 0.0
    score = float(np.clip(100 - 4 * float(residual.std(ddof=0)) - 8 * median_change, 0, 100))

    # Count held levels: steady stretches of at least MIN_LEVEL_HOLD whose medians differ by LEVEL_STEP_C or more.
    run_id = (steady != steady.shift(fill_value=False)).cumsum()[steady]
    levels = []
    for r in run_id.unique():
        stretch = series[steady][run_id == r]
        if stretch.index[-1] - stretch.index[0] >= pd.Timedelta(MIN_LEVEL_HOLD):
            levels.append(float(stretch.median()))
    level_changes = sum(1 for a, b in zip(levels, levels[1:]) if abs(b - a) >= LEVEL_STEP_C)
    return score, level_changes, float(steady.mean())


def analyse(prepared: pd.DataFrame, role: str) -> PitResult:
    if prepared.empty:
        raise ValueError("The selected environmental profile contains no valid data.")

    values = prepared["temperature_c"].astype(float)
    stddev = float(values.std(ddof=0))
    stability_score, level_changes, steady_share = steady_stability(prepared)

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
        level_changes=level_changes,
        steady_share=steady_share,
    )
