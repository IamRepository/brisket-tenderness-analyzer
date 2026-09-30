from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import brisket_engine as meat


ROLES = [
    "🥩 Brisket - Point",
    "🥩 Brisket - Flat",
    "🔥 Cook Environment - PID",
    "🌡 Cook Environment - Grate",
    "♨ Hold Environment - Probe",
    "🚫 Ignore",
]


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


def classify_columns(df: pd.DataFrame, timestamp_col: str) -> pd.DataFrame:
    """Suggest a role for each usable temperature column.

    The suggestions are intentionally conservative. Probe 1 and Probe 2 do not
    reliably identify Point or Flat, so generic meat-probe columns default to
    Brisket - Flat and must be reviewed by the user.
    """
    rows = []

    for col in df.columns:
        if col == timestamp_col:
            continue

        values = meat.parse_temp(df[col])
        valid = values.dropna()

        if len(valid) < 2:
            continue

        name = str(col).strip().lower()

        if "point" in name:
            role = "🥩 Brisket - Point"
            confidence = 95

        elif "flat" in name:
            role = "🥩 Brisket - Flat"
            confidence = 95

        elif any(
            hint in name
            for hint in (
                "hold environment",
                "hold probe",
                "holding probe",
                "holding oven",
                "oven probe",
                "hold oven",
                "cvap",
                "holding cabinet",
            )
        ):
            role = "♨ Hold Environment - Probe"
            confidence = 94

        elif any(
            hint in name
            for hint in (
                "grate",
                "ambient",
                "pit probe",
                "chamber probe",
            )
        ):
            role = "🌡 Cook Environment - Grate"
            confidence = 93

        elif any(
            hint in name
            for hint in (
                "cavity temperature",
                "controller",
                "smoker",
                "smoque",
                "built-in pit",
                "pid",
            )
        ):
            role = "🔥 Cook Environment - PID"
            confidence = 92

        elif any(
            hint in name
            for hint in (
                "meat",
                "internal",
                "food",
                "brisket",
                "probe",
            )
        ):
            # A generic probe name cannot reliably indicate Point versus Flat.
            # Use Flat as a neutral default and require user review.
            role = "🥩 Brisket - Flat"
            confidence = 65

        else:
            window = min(21, max(3, len(valid) // 50))
            smooth = valid.rolling(
                window,
                center=True,
                min_periods=1,
            ).median()

            start_window = max(2, len(smooth) // 20)
            rise = float(
                smooth.max()
                - smooth.iloc[:start_window].median()
            )
            fluctuation = float(valid.diff().abs().median())

            if rise >= 20:
                role = "🥩 Brisket - Flat"
                confidence = 55
            elif fluctuation >= 0.5:
                role = "🌡 Cook Environment - Grate"
                confidence = 60
            else:
                role = "🔥 Cook Environment - PID"
                confidence = 55

        rows.append(
            {
                "Column": col,
                "Suggested role": role,
                "Confidence": confidence,
            }
        )

    return pd.DataFrame(rows)


def prepare(
    df: pd.DataFrame,
    timestamp_col: str,
    temp_col: str,
) -> pd.DataFrame:
    """Prepare and validate one environmental temperature stream."""
    prepared = pd.DataFrame(
        {
            "timestamp": meat.parse_ts(df[timestamp_col]),
            "temperature_c": meat.parse_temp(df[temp_col]),
        }
    ).dropna()

    prepared = prepared[
        prepared["temperature_c"].between(-20, 400)
    ]

    return (
        prepared.sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )


def analyse(prepared: pd.DataFrame, role: str) -> PitResult:
    """Calculate descriptive statistics for an environmental profile."""
    if prepared.empty:
        raise ValueError("The selected environmental profile contains no valid data.")

    values = prepared["temperature_c"].astype(float)
    stddev = float(values.std(ddof=0))
    median_change = (
        float(values.diff().abs().median())
        if len(values) > 1
        else 0.0
    )

    stability_score = float(
        np.clip(
            100 - 4 * stddev - 8 * median_change,
            0,
            100,
        )
    )

    lid_events = pd.DataFrame()

    if role == "🌡 Cook Environment - Grate" and len(prepared) > 4:
        differences = values.diff(5)
        event_mask = differences <= -12

        candidates = prepared.loc[
            event_mask,
            ["timestamp", "temperature_c"],
        ].copy()

        if not candidates.empty:
            candidates["Drop °C"] = (
                -differences.loc[event_mask]
            ).round(1).to_numpy()

            lid_events = candidates.rename(
                columns={
                    "timestamp": "Detected time",
                    "temperature_c": "Temperature °C",
                }
            ).head(20)

    return PitResult(
        role=role,
        timeline=prepared,
        average=float(values.mean()),
        minimum=float(values.min()),
        maximum=float(values.max()),
        stddev=stddev,
        stability_score=stability_score,
        lid_events=lid_events,
    )


def align(
    meat_timeline: pd.DataFrame,
    environment_results: dict[str, PitResult],
) -> pd.DataFrame:
    """Align environmental profiles to one meat timeline for comparison.

    Alignment uses nearest timestamps with a tolerance based on each
    environment stream's median sampling interval. The function preserves
    missing periods rather than interpolating across smoker-to-hold transfers.
    """
    overlay = (
        meat_timeline[["Timestamp", "Temperature °C"]]
        .rename(
            columns={
                "Timestamp": "timestamp",
                "Temperature °C": "Meat temperature",
            }
        )
        .sort_values("timestamp")
    )

    for label, result in environment_results.items():
        stream = (
            result.timeline
            .rename(columns={"temperature_c": label})
            .sort_values("timestamp")
        )

        intervals = (
            stream["timestamp"]
            .diff()
            .dt.total_seconds()
            .dropna()
        )
        intervals = intervals[intervals > 0]

        tolerance_seconds = (
            max(float(intervals.median()) * 2, 60.0)
            if not intervals.empty
            else 60.0
        )

        overlay = pd.merge_asof(
            overlay.sort_values("timestamp"),
            stream[["timestamp", label]],
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta(seconds=tolerance_seconds),
        )

    return overlay
