from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Iterable
import re

import numpy as np
import pandas as pd

# Continuous Celsius boundaries converted from the published Fahrenheit anchor points.
BAND_LOWER_C = np.array([
    60.0,
    65.5555555556,
    71.1111111111,
    76.6666666667,
    82.2222222222,
    87.7777777778,
    90.5555555556,
    93.3333333333,
    96.1111111111,
    98.8888888889,
])

# Zone-weighted rendering rates from the existing Celsius calculator.
ZONE_RATES_PER_HOUR = np.array([
    0.0145, 0.0245, 0.0390, 0.0680, 0.1305,
    0.2080, 0.2900, 0.4300, 0.6300, 0.7500,
])

BAND_LABELS = [
    "60.0 to <65.6°C",
    "65.6 to <71.1°C",
    "71.1 to <76.7°C",
    "76.7 to <82.2°C",
    "82.2 to <87.8°C",
    "87.8 to <90.6°C",
    "90.6 to <93.3°C",
    "93.3 to <96.1°C",
    "96.1 to <98.9°C",
    "98.9°C and above",
]

TIMESTAMP_HINTS = ("timestamp", "date time", "datetime", "time", "date")
TEMPERATURE_HINTS = ("temperature", "temp", "probe", "celsius", "°c")


@dataclass(frozen=True)
class ParseReport:
    source_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_timestamps: int
    first_timestamp: pd.Timestamp | None
    last_timestamp: pd.Timestamp | None


@dataclass(frozen=True)
class AnalysisResult:
    summary: pd.DataFrame
    timeline: pd.DataFrame
    parse_report: ParseReport
    excluded_gap_count: int
    excluded_gap_hours: float
    below_model_hours: float
    analysed_hours: float
    cook_rendering: float
    hold_rendering: float
    total_rendering: float
    assessment: str


def read_probe_file(file_obj: BinaryIO, filename: str) -> dict[str, pd.DataFrame]:
    """Read CSV or Excel and return {sheet_name: dataframe}."""
    suffix = Path(filename).suffix.lower()
    data = file_obj.read()
    file_obj.seek(0)
    bio = BytesIO(data)

    if suffix == ".csv":
        # Try automatic delimiter detection, then common fallbacks.
        try:
            df = pd.read_csv(bio, sep=None, engine="python")
        except Exception:
            bio.seek(0)
            df = pd.read_csv(bio)
        return {"CSV": df}

    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(bio, sheet_name=None, engine=None)

    raise ValueError("Supported formats are .xlsx, .xlsm, .xls and .csv.")


def detect_columns(df: pd.DataFrame) -> tuple[str, list[str]]:
    """Return timestamp column and likely temperature columns."""
    if df.empty:
        raise ValueError("The selected sheet contains no rows.")

    columns = [str(c) for c in df.columns]
    lower = {str(c): str(c).strip().lower() for c in df.columns}

    timestamp_col = next(
        (c for c in columns if any(h in lower[c] for h in TIMESTAMP_HINTS)),
        None,
    )

    if timestamp_col is None:
        # Pick the column with the highest timestamp parse success in the first 200 rows.
        scores: list[tuple[float, str]] = []
        for c in columns:
            sample = df[c].head(200)
            parsed = parse_timestamp_series(sample)
            scores.append((float(parsed.notna().mean()), c))
        score, timestamp_col = max(scores, default=(0.0, ""))
        if score < 0.25:
            raise ValueError("A timestamp column could not be detected.")

    temperature_cols = [
        c for c in columns
        if c != timestamp_col and any(h in lower[c] for h in TEMPERATURE_HINTS)
    ]

    if not temperature_cols:
        numeric_scores: list[tuple[float, str]] = []
        for c in columns:
            if c == timestamp_col:
                continue
            values = parse_temperature_series(df[c].head(200))
            plausible = values.between(-20, 150)
            numeric_scores.append((float(plausible.mean()), c))
        numeric_scores.sort(reverse=True)
        temperature_cols = [c for score, c in numeric_scores if score >= 0.25]

    if not temperature_cols:
        raise ValueError("A temperature column could not be detected.")

    return timestamp_col, temperature_cols


def parse_timestamp_series(series: pd.Series) -> pd.Series:
    """Parse Excel datetimes or DD/MM/YYYY HH:MM:SS with optional timezone suffix."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")

    text = series.astype("string").str.strip()
    text = text.str.replace(r"\s+(CEST|CET|UTC|GMT)$", "", regex=True, case=False)
    text = text.str.replace("T", " ", regex=False)

    # dayfirst=True fits the provided DD/MM/YYYY stream and still accepts Excel dates.
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)

    # Numeric Excel serial dates, if present.
    numeric = pd.to_numeric(series, errors="coerce")
    serial_mask = parsed.isna() & numeric.between(20000, 100000)
    if serial_mask.any():
        parsed.loc[serial_mask] = pd.to_datetime(
            numeric.loc[serial_mask], unit="D", origin="1899-12-30", errors="coerce"
        )
    return parsed


def parse_temperature_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    text = series.astype("string")
    text = text.str.replace("°C", "", regex=False, case=False)
    text = text.str.replace("Celsius", "", regex=False, case=False)
    text = text.str.strip()
    return pd.to_numeric(text, errors="coerce")


def prepare_probe_data(
    df: pd.DataFrame,
    timestamp_col: str,
    temperature_col: str,
) -> tuple[pd.DataFrame, ParseReport]:
    raw = pd.DataFrame({
        "timestamp": parse_timestamp_series(df[timestamp_col]),
        "temperature_c": parse_temperature_series(df[temperature_col]),
    })
    source_rows = len(raw)
    valid_mask = raw["timestamp"].notna() & raw["temperature_c"].between(-20, 150)
    valid = raw.loc[valid_mask].copy()
    invalid_rows = int((~valid_mask).sum())
    valid.sort_values("timestamp", inplace=True, kind="stable")

    duplicate_timestamps = int(valid.duplicated("timestamp", keep="last").sum())
    valid.drop_duplicates("timestamp", keep="last", inplace=True)
    valid.reset_index(drop=True, inplace=True)

    report = ParseReport(
        source_rows=source_rows,
        valid_rows=len(valid),
        invalid_rows=invalid_rows,
        duplicate_timestamps=duplicate_timestamps,
        first_timestamp=valid["timestamp"].min() if not valid.empty else None,
        last_timestamp=valid["timestamp"].max() if not valid.empty else None,
    )
    return valid, report


def _band_index(temperature_c: float) -> int:
    if temperature_c < BAND_LOWER_C[0]:
        return -1
    return min(int(np.searchsorted(BAND_LOWER_C, temperature_c, side="right") - 1), 9)


def tenderness_assessment(rendering_fraction: float) -> str:
    percent = rendering_fraction * 100.0
    if percent < 80:
        return "Underdone and tight"
    if percent < 95:
        return "Slightly tight but sliceable"
    if percent <= 105:
        return "Ideal tenderness"
    if percent <= 120:
        return "Very soft and potentially overdone"
    return "Increased risk of mushy or over-rendered texture"


def analyse_probe(
    prepared: pd.DataFrame,
    report: ParseReport,
    mode: str,
    pull_timestamp: datetime | pd.Timestamp | None = None,
    max_gap_seconds: float = 10.0,
) -> AnalysisResult:
    """
    Analyse a single probe.

    mode: 'Cook only', 'Hold / cooldown only', or 'Split at pull timestamp'.
    Each interval is assigned using the earlier reading's temperature.
    If an interval crosses the pull timestamp, its duration is split across phases.
    """
    if len(prepared) < 2:
        raise ValueError("At least two valid readings are required.")
    if max_gap_seconds <= 0:
        raise ValueError("Maximum gap must be greater than zero.")
    if mode == "Split at pull timestamp" and pull_timestamp is None:
        raise ValueError("A pull timestamp is required for split mode.")

    pull = pd.Timestamp(pull_timestamp) if pull_timestamp is not None else None
    phase_seconds = np.zeros((2, 10), dtype=float)  # Cook, Hold
    below_model_seconds = 0.0
    excluded_gap_seconds = 0.0
    excluded_gap_count = 0

    rows: list[dict] = []
    timestamps = prepared["timestamp"].tolist()
    temperatures = prepared["temperature_c"].astype(float).tolist()

    for idx in range(len(prepared) - 1):
        start = pd.Timestamp(timestamps[idx])
        end = pd.Timestamp(timestamps[idx + 1])
        seconds = (end - start).total_seconds()
        temp = temperatures[idx]

        row = {
            "timestamp": start,
            "temperature_c": temp,
            "next_timestamp": end,
            "elapsed_seconds": seconds,
            "status": "Analysed",
            "phase": "",
            "band": "",
        }

        if seconds <= 0:
            row["status"] = "Excluded: non-positive interval"
            rows.append(row)
            continue
        if seconds > max_gap_seconds:
            excluded_gap_count += 1
            excluded_gap_seconds += seconds
            row["status"] = "Excluded: recording gap"
            rows.append(row)
            continue

        band = _band_index(temp)
        if band < 0:
            below_model_seconds += seconds
            row["status"] = "Below model range"
            row["band"] = "Below 60.0°C"
            rows.append(row)
            continue

        row["band"] = BAND_LABELS[band]

        if mode == "Cook only":
            phase_seconds[0, band] += seconds
            row["phase"] = "Cook"
        elif mode == "Hold / cooldown only":
            phase_seconds[1, band] += seconds
            row["phase"] = "Hold / cooldown"
        elif mode == "Split at pull timestamp":
            assert pull is not None
            if end <= pull:
                phase_seconds[0, band] += seconds
                row["phase"] = "Cook"
            elif start >= pull:
                phase_seconds[1, band] += seconds
                row["phase"] = "Hold / cooldown"
            else:
                cook_seconds = max((pull - start).total_seconds(), 0.0)
                hold_seconds = max(seconds - cook_seconds, 0.0)
                phase_seconds[0, band] += cook_seconds
                phase_seconds[1, band] += hold_seconds
                row["phase"] = "Split across pull timestamp"
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        rows.append(row)

    records: list[dict] = []
    for phase_idx, phase in enumerate(("Cook", "Hold / cooldown")):
        for band in range(10):
            hours = phase_seconds[phase_idx, band] / 3600.0
            rate = ZONE_RATES_PER_HOUR[band]
            contribution = hours * rate
            records.append({
                "Phase": phase,
                "Band": band + 1,
                "Temperature range": BAND_LABELS[band],
                "Duration hours": hours,
                "Rate per hour": rate,
                "Tenderness contribution": contribution,
            })

    summary = pd.DataFrame(records)
    cook_rendering = float(summary.loc[summary["Phase"] == "Cook", "Tenderness contribution"].sum())
    hold_rendering = float(summary.loc[summary["Phase"] == "Hold / cooldown", "Tenderness contribution"].sum())
    total_rendering = cook_rendering + hold_rendering

    return AnalysisResult(
        summary=summary,
        timeline=pd.DataFrame(rows),
        parse_report=report,
        excluded_gap_count=excluded_gap_count,
        excluded_gap_hours=excluded_gap_seconds / 3600.0,
        below_model_hours=below_model_seconds / 3600.0,
        analysed_hours=float(phase_seconds.sum() / 3600.0),
        cook_rendering=cook_rendering,
        hold_rendering=hold_rendering,
        total_rendering=total_rendering,
        assessment=tenderness_assessment(total_rendering),
    )


def results_to_excel(results: dict[str, AnalysisResult]) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        overview_rows = []
        for probe_name, result in results.items():
            report = result.parse_report
            overview_rows.append({
                "Probe": probe_name,
                "First timestamp": report.first_timestamp,
                "Last timestamp": report.last_timestamp,
                "Valid rows": report.valid_rows,
                "Invalid rows": report.invalid_rows,
                "Duplicate timestamps": report.duplicate_timestamps,
                "Excluded gaps": result.excluded_gap_count,
                "Excluded gap hours": result.excluded_gap_hours,
                "Below 60°C hours": result.below_model_hours,
                "Analysed hours": result.analysed_hours,
                "Cook rendering": result.cook_rendering,
                "Hold rendering": result.hold_rendering,
                "Total rendering": result.total_rendering,
                "Assessment": result.assessment,
            })

            safe_name = re.sub(r"[^A-Za-z0-9 _-]", "", probe_name)[:20] or "Probe"
            result.summary.to_excel(writer, sheet_name=f"{safe_name} Summary"[:31], index=False)
            result.timeline.to_excel(writer, sheet_name=f"{safe_name} Timeline"[:31], index=False)

        overview = pd.DataFrame(overview_rows)
        overview.to_excel(writer, sheet_name="Overview", index=False)

        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            for column in ws.columns:
                max_len = min(max((len(str(cell.value)) if cell.value is not None else 0) for cell in column) + 2, 45)
                ws.column_dimensions[column[0].column_letter].width = max_len

        overview_ws = writer.book["Overview"]
        for col_name in ("K", "L", "M"):
            for cell in overview_ws[col_name][1:]:
                cell.number_format = "0.0%"

    return output.getvalue()
