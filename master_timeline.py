from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import pandas as pd


@dataclass(frozen=True)
class MasterTimeline:
    session_start: pd.Timestamp
    session_end: pd.Timestamp
    transfer_time: pd.Timestamp | None

    @property
    def duration_hours(self) -> float:
        return max(
            (self.session_end - self.session_start).total_seconds() / 3600.0,
            0.0,
        )

    @property
    def transfer_elapsed_hours(self) -> float | None:
        if self.transfer_time is None:
            return None
        return (
            self.transfer_time - self.session_start
        ).total_seconds() / 3600.0


def _valid_timestamps(frame: pd.DataFrame, timestamp_column: str) -> pd.Series:
    if frame is None or frame.empty or timestamp_column not in frame.columns:
        return pd.Series(dtype="datetime64[ns]")

    timestamps = pd.to_datetime(frame[timestamp_column], errors="coerce")
    return timestamps.dropna().sort_values()


def build_master_timeline(
    streams: Iterable[tuple[pd.DataFrame, str]],
    transfer_time=None,
) -> MasterTimeline:
    """Create one session timeline from every selected meat/environment stream."""
    starts: list[pd.Timestamp] = []
    ends: list[pd.Timestamp] = []

    for frame, timestamp_column in streams:
        timestamps = _valid_timestamps(frame, timestamp_column)
        if timestamps.empty:
            continue
        starts.append(pd.Timestamp(timestamps.iloc[0]))
        ends.append(pd.Timestamp(timestamps.iloc[-1]))

    if not starts:
        raise ValueError("No valid timestamps were available for the master session timeline.")

    session_start = min(starts)
    session_end = max(ends)
    parsed_transfer = (
        pd.Timestamp(transfer_time) if transfer_time is not None else None
    )

    if parsed_transfer is not None and not (
        session_start <= parsed_transfer <= session_end
    ):
        raise ValueError(
            "The smoker-to-hold transfer timestamp falls outside the master session."
        )

    return MasterTimeline(
        session_start=session_start,
        session_end=session_end,
        transfer_time=parsed_transfer,
    )


def add_master_elapsed_time(
    frame: pd.DataFrame,
    master: MasterTimeline,
    timestamp_column: str = "timestamp",
    elapsed_column: str = "Elapsed session hours",
) -> pd.DataFrame:
    """Add elapsed time from cook/session start without altering raw timestamps."""
    output = frame.copy()
    output[timestamp_column] = pd.to_datetime(
        output[timestamp_column], errors="coerce"
    )
    output[elapsed_column] = (
        output[timestamp_column] - master.session_start
    ).dt.total_seconds() / 3600.0
    return output


def align_streams_to_master(
    streams: Mapping[str, tuple[pd.DataFrame, str, str]],
    master: MasterTimeline,
) -> pd.DataFrame:
    """Return tidy chart data aligned to one master-session clock.

    Mapping values are: (dataframe, timestamp column, temperature column).
    No interpolation or artificial time shifting is performed.
    """
    frames: list[pd.DataFrame] = []

    for stream_name, (frame, timestamp_column, temperature_column) in streams.items():
        if frame is None or frame.empty:
            continue
        if timestamp_column not in frame.columns or temperature_column not in frame.columns:
            continue

        selected = frame[[timestamp_column, temperature_column]].copy()
        selected.columns = ["Timestamp", "Temperature °C"]
        selected["Timestamp"] = pd.to_datetime(selected["Timestamp"], errors="coerce")
        selected["Temperature °C"] = pd.to_numeric(
            selected["Temperature °C"], errors="coerce"
        )
        selected.dropna(subset=["Timestamp", "Temperature °C"], inplace=True)

        selected["Elapsed session hours"] = (
            selected["Timestamp"] - master.session_start
        ).dt.total_seconds() / 3600.0
        selected["Stream"] = stream_name
        frames.append(selected)

    if not frames:
        return pd.DataFrame(
            columns=[
                "Timestamp",
                "Temperature °C",
                "Elapsed session hours",
                "Stream",
            ]
        )

    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values(["Elapsed session hours", "Stream"], inplace=True)
    return combined


def add_transfer_marker(figure, master: MasterTimeline, label: str = "Smoker to hold"):
    """Add the transfer event to a Plotly figure using master elapsed hours."""
    transfer_hour = master.transfer_elapsed_hours
    if transfer_hour is None:
        return figure

    figure.add_vline(
        x=transfer_hour,
        line_width=2,
        line_dash="dash",
        line_color="#8b3f2f",
    )
    figure.add_annotation(
        x=transfer_hour,
        y=1.0,
        yref="paper",
        text=label,
        showarrow=False,
        xanchor="left",
        yanchor="bottom",
        font={"color": "#8b3f2f"},
    )
    return figure
