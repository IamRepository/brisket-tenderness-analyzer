from __future__ import annotations

from datetime import datetime
from io import BytesIO

import pandas as pd
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

DARK = colors.HexColor("#27343B")
ACCENT = colors.HexColor("#A94722")
LIGHT = colors.HexColor("#F3F5F6")
GRID = colors.HexColor("#D6DCE0")
TRANSFER = colors.HexColor("#7A3E9D")
PALETTE = [colors.HexColor(x) for x in ("#C6532B", "#2F6B9A", "#5B7F4B", "#8A5A9B", "#C18B2F")]
CONTENT_WIDTH = A4[0] - 28 * mm

STYLES = getSampleStyleSheet()
STYLES.add(ParagraphStyle(name="ReportTitle", parent=STYLES["Title"], fontSize=21, leading=25, textColor=DARK))
STYLES.add(ParagraphStyle(name="Section", parent=STYLES["Heading2"], fontSize=14, leading=17, textColor=ACCENT, spaceBefore=8, spaceAfter=6))
STYLES.add(ParagraphStyle(name="Small", parent=STYLES["BodyText"], fontSize=7.4, leading=9))
STYLES.add(ParagraphStyle(name="Cell", parent=STYLES["BodyText"], fontSize=7.1, leading=8.6))


def _text(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y %H:%M:%S")
    text = str(value)
    for symbol in ("🥩", "🔥", "🌡", "♨", "🚫", "🟦", "🟩"):
        text = text.replace(symbol, "")
    return text.strip()


def _temperature(value):
    """Format every reported temperature consistently to one decimal place."""
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.1f}"


def _short_label(value):
    text = _text(value)
    return text.split(" — ", 1)[0].strip() if " — " in text else text


def _source(value):
    text = _text(value)
    if "/" not in text:
        return text
    file_label, column = [part.strip() for part in text.split("/", 1)]
    lowered = column.lower()
    if lowered.startswith("poin") and set(lowered[4:]) <= {"t"}:
        column = "Point"
    elif lowered == "enviroment":
        column = "Environment"
    return f"{file_label} / {column}"


def _environment_label(value, stage=None):
    """Use one environment naming convention in tables and chart legends."""
    text = _short_label(value).lower()
    if "composite" in text:
        return f"Composite Environment ({stage})" if stage else "Composite Environment"
    if "hold" in text:
        return "Hold Environment"
    if "grate" in text:
        return "Cook Environment - Grate"
    if "pid" in text or "controller" in text:
        return "Cook Environment - PID"
    return _short_label(value)


def _stage_label(label, result):
    role = _short_label(label)
    session = result.detection.session_type
    if session == "Cook + Hold":
        return f"{role} (Cook + Hold)"
    if session in ("Hold Only", "Calibration / Hold Test"):
        return f"{role} (Hold only)"
    if session == "Cook Only":
        return f"{role} (Cook only)"
    return f"{role} ({session})"


def _table(rows, widths, font_size=7.1, header=True):
    cells = [[Paragraph(_text(cell), STYLES["Cell"]) for cell in row] for row in rows]
    result = Table(cells, colWidths=widths, repeatRows=1 if header else 0, splitByRow=1, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, 0), DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ])
    result.setStyle(TableStyle(commands))
    return result


def _chart(series, title, master_start=None, transfer_time=None, y_title="Temperature (C)"):
    drawing = Drawing(CONTENT_WIDTH, 94 * mm)
    chart = LinePlot()
    chart.x = 15 * mm
    chart.y = 27 * mm
    chart.width = CONTENT_WIDTH - 27 * mm
    chart.height = 50 * mm

    data, names, all_x, all_y = [], [], [], []
    for name, frame, time_col, value_col in series:
        clean = frame[[time_col, value_col]].dropna().sort_values(time_col)
        if clean.empty:
            continue
        if len(clean) > 350:
            clean = clean.iloc[::max(len(clean) // 350, 1)]
        origin = pd.Timestamp(master_start) if master_start is not None else clean[time_col].min()
        x_values = (clean[time_col] - origin).dt.total_seconds() / 3600.0
        y_values = pd.to_numeric(clean[value_col], errors="coerce")
        points = [(float(x), float(y)) for x, y in zip(x_values, y_values) if pd.notna(y)]
        if points:
            data.append(points)
            names.append(_short_label(name))
            all_x.extend(x for x, _ in points)
            all_y.extend(y for _, y in points)

    if not data:
        return Paragraph("No chart data available.", STYLES["BodyText"])

    x_min, x_max = min(all_x), max(all_x)
    chart.data = data
    chart.xValueAxis.valueMin = x_min
    chart.xValueAxis.valueMax = x_max if x_max > x_min else x_min + 1
    chart.xValueAxis.labelTextFormat = "%0.1f"
    chart.xValueAxis.labels.fontSize = 7
    chart.yValueAxis.valueMin = min(all_y) - 2
    chart.yValueAxis.valueMax = max(all_y) + 2
    chart.yValueAxis.labels.fontSize = 7

    for index in range(len(data)):
        chart.lines[index].strokeColor = PALETTE[index % len(PALETTE)]
        chart.lines[index].strokeWidth = 1.2
        chart.lines[index].symbol = makeMarker("FilledCircle")
        chart.lines[index].symbol.size = 1.7

    legend = Legend()
    legend.x = 15 * mm
    legend.y = 17 * mm
    legend.fontSize = 6.2
    legend.deltax = 76
    legend.deltay = 9
    legend.columnMaximum = 2
    legend.colorNamePairs = [(PALETTE[i % len(PALETTE)], names[i]) for i in range(len(names))]

    drawing.add(chart)
    drawing.add(legend)
    drawing.add(String(15 * mm, 83 * mm, _text(title), fontName="Helvetica-Bold", fontSize=10.5, fillColor=DARK))
    drawing.add(String(15 * mm, 4 * mm, "Elapsed time from master session start (hours)", fontSize=6.5, fillColor=DARK))
    drawing.add(String(1 * mm, 48 * mm, y_title, fontSize=6.5, fillColor=DARK))

    if transfer_time is not None and master_start is not None:
        transfer_hour = (pd.Timestamp(transfer_time) - pd.Timestamp(master_start)).total_seconds() / 3600.0
        if x_min <= transfer_hour <= x_max:
            fraction = (transfer_hour - x_min) / max(x_max - x_min, 1e-9)
            marker_x = chart.x + fraction * chart.width
            drawing.add(Line(marker_x, chart.y, marker_x, chart.y + chart.height, strokeColor=TRANSFER, strokeWidth=1, strokeDashArray=[3, 2]))
            drawing.add(String(marker_x + 2, chart.y + chart.height - 7, "Transfer", fontSize=6.5, fillColor=TRANSFER))

    return drawing


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(DARK)
    canvas.drawString(14 * mm, 8 * mm, "Brisket Session Analyser - analytical report")
    canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _interval(seconds):
    if seconds is None:
        return "Unknown"
    if seconds < 60:
        return f"{seconds:.0f} sec"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"


def _duration(frame):
    if frame is None or len(frame) < 2:
        return 0.0
    return (frame["timestamp"].iloc[-1] - frame["timestamp"].iloc[0]).total_seconds() / 3600.0


def build_pdf_report(app_version, configuration, meat_results, stats, environment_results, transfer_time, environment_sources=None, master_start=None, cook_aggregate=None, hold_aggregate=None):
    environment_sources = environment_sources or {}
    if master_start is None:
        starts = [item["valid"]["timestamp"].min() for item in meat_results.values() if not item["valid"].empty]
        starts += [result.timeline["timestamp"].min() for result in environment_results.values() if not result.timeline.empty]
        master_start = min(starts) if starts else None

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=13 * mm, bottomMargin=15 * mm)
    story = []

    story.extend([
        Paragraph(f"Brisket Session Analyser {app_version}", STYLES["ReportTitle"]),
        Paragraph("Full session analysis report", STYLES["Heading2"]),
        Paragraph(f"Generated: {datetime.now():%d/%m/%Y %H:%M:%S}", STYLES["Small"]),
        Paragraph("Methodology", STYLES["Section"]),
        Paragraph("Independent implementation based on brisket time-temperature and hot-hold concepts shared by Steve Gow. Results are analytical estimates.", STYLES["BodyText"]),
        Paragraph("Configuration", STYLES["Section"]),
        _table([["Role", "Source"]] + [[row.get("Role"), _source(row.get("Source"))] for row in configuration], [78 * mm, 102 * mm]),
        Spacer(1, 3 * mm),
        _table([["Master timeline start", _text(master_start)], ["Derived smoker-to-hold boundary", _text(transfer_time)]], [72 * mm, 108 * mm], header=False),
    ])

    timing = [["Profile", "Source", "Session", "Conf.", "Pull", "Cook h", "Hold h"]]
    temperatures = [["Profile", "Peak C", "Avg C", "Min C", "Cook avg C", "Hold avg C", "Sampling", "Gap"]]
    comparison = [["Profile", "Source", "Cook", "Hold", "Total", "Assessment", "h"]]
    meat_series = []

    for label, item in meat_results.items():
        result, stat, sample = item["result"], stats[label], item["sample"]
        display = _stage_label(label, result)
        timing.append([display, _source(item["source"]), result.detection.session_type, f"{result.detection.confidence}%", _text(result.detection.pull_timestamp), f"{stat['cook_h']:.2f}", f"{stat['hold_h']:.2f}"])
        temperatures.append([display, _temperature(result.detection.peak_temperature), _temperature(result.detection.average_temperature), _temperature(result.detection.minimum_temperature), _temperature(stat["cook_avg"]), _temperature(stat["hold_avg"]), _interval(sample["normal"]), _interval(sample["threshold"])])
        comparison.append([display, _source(item["source"]), f"{result.cook:.1%}", f"{result.hold:.1%}", f"{result.total:.1%}", result.assessment if result.complete else "Partial session", f"{result.analysed_hours:.2f}"])
        meat_series.append((display, item["valid"], "timestamp", "temperature_c"))

    story.extend([
        PageBreak(), Paragraph("Session detection and timing", STYLES["Section"]), _table(timing, [42*mm, 31*mm, 28*mm, 14*mm, 32*mm, 16*mm, 16*mm], 6.8),
        Spacer(1, 5*mm), Paragraph("Temperature and sampling summary", STYLES["Section"]), _table(temperatures, [48*mm, 17*mm, 17*mm, 17*mm, 22*mm, 22*mm, 21*mm, 21*mm], 6.8),
        PageBreak(), Paragraph("Brisket probe comparison", STYLES["Section"]), _table(comparison, [43*mm, 31*mm, 17*mm, 17*mm, 17*mm, 42*mm, 14*mm], 6.8),
        PageBreak(), Paragraph("Brisket temperature profiles", STYLES["Section"]), _chart(meat_series, "Brisket Point and Flat temperature profiles", master_start, transfer_time),
        PageBreak(), Paragraph("Environment analysis", STYLES["Section"]),
    ])

    if environment_results:
        environment_rows = [["Stream", "Source", "Stage", "Start", "End", "h", "Avg C", "Min C", "Max C", "Stab."]]
        environment_series = []
        for label, result in environment_results.items():
            frame = result.timeline
            normalised = _environment_label(result.role)
            stage_name = "Hold" if normalised == "Hold Environment" else "Cook"
            environment_rows.append([normalised, _source(environment_sources.get(label, "N/A")), stage_name, _text(frame["timestamp"].min()), _text(frame["timestamp"].max()), f"{_duration(frame):.2f}", _temperature(result.average), _temperature(result.minimum), _temperature(result.maximum), f"{result.stability_score:.0f}/100"])
            environment_series.append((normalised, frame, "timestamp", "temperature_c"))
        story.extend([_table(environment_rows, [27*mm, 28*mm, 15*mm, 31*mm, 31*mm, 11*mm, 11*mm, 11*mm, 11*mm, 15*mm], 6.2), Spacer(1, 4*mm), _chart(environment_series, "Cook and Hold Environment profiles", master_start, transfer_time)])

    aggregate_rows = [["Stage", "Start", "End", "Readings", "Average C", "Sensors"]]
    composite_series = []
    for stage_name, frame in (("Cook", cook_aggregate), ("Hold", hold_aggregate)):
        if frame is not None and not frame.empty:
            composite_series.append((_environment_label("Composite", stage_name), frame, "timestamp", "Environment aggregate °C"))
            aggregate_rows.append([stage_name, _text(frame["timestamp"].min()), _text(frame["timestamp"].max()), len(frame), _temperature(frame["Environment aggregate °C"].mean()), f"{frame['Available sensors'].mean():.2f}"])
    if composite_series:
        story.extend([PageBreak(), Paragraph("Composite environment timeline", STYLES["Section"]), Paragraph("Cook and Hold segments share the master timeline. The transfer gap remains visible.", STYLES["Small"]), _table(aggregate_rows, [22*mm, 38*mm, 38*mm, 20*mm, 30*mm, 32*mm]), Spacer(1, 4*mm), _chart(composite_series, "Composite Environment", master_start, transfer_time)])

    for label, item in meat_results.items():
        result, stat = item["result"], stats[label]
        display = _stage_label(label, result)
        metrics = [["Metric", "Value", "Metric", "Value"], ["Source", _source(item["source"]), "Session", result.detection.session_type], ["Confidence", f"{result.detection.confidence}%", "Detected pull", _text(result.detection.pull_timestamp)], ["Cook duration", f"{stat['cook_h']:.2f} h", "Hold duration", f"{stat['hold_h']:.2f} h"], ["Cook contribution", f"{result.cook:.1%}", "Hold contribution", f"{result.hold:.1%}"], ["Recorded total", f"{result.total:.1%}", "Assessment", result.assessment if result.complete else "Partial session"]]
        bands = result.summary[result.summary["Duration hours"] > 0]
        band_rows = [["Phase", "Band", "Temperature range", "Duration h", "Rate/h", "Contribution"]] + [[row["Phase"], row["Band"], row["Temperature range"], f"{row['Duration hours']:.3f}", f"{row['Rate per hour']:.1%}", f"{row['Tenderness contribution']:.1%}"] for _, row in bands.iterrows()]
        story.extend([PageBreak(), Paragraph(display, STYLES["Section"]), Paragraph(f"Source: {_source(item['source'])}", STYLES["Small"]), _table(metrics, [29*mm, 61*mm, 29*mm, 61*mm]), Spacer(1, 4*mm), _chart([(display, item["valid"], "timestamp", "temperature_c")], f"Temperature profile - {display}", master_start, transfer_time)])
        if not bands.empty:
            story.extend([CondPageBreak(68*mm), Paragraph("Rendering band calculation", STYLES["Section"]), _table(band_rows, [27*mm, 12*mm, 49*mm, 25*mm, 24*mm, 35*mm], 6.8)])

    story.extend([PageBreak(), Paragraph("Interpretation notes", STYLES["Section"]), Paragraph("All report charts use one shared master session timeline. The transfer marker indicates the derived smoker-to-hold boundary. Missing values are not interpolated across the transfer.", STYLES["BodyText"] )])
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()
