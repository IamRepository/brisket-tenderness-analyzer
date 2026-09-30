from __future__ import annotations

from io import BytesIO
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.widgets.markers import makeMarker

ACCENT = colors.HexColor("#A94722")
DARK = colors.HexColor("#27343B")
LIGHT = colors.HexColor("#F3F5F6")
GRID = colors.HexColor("#D6DCE0")
PALETTE = [colors.HexColor(x) for x in ("#C6532B", "#2F6B9A", "#5B7F4B", "#8A5A9B", "#C18B2F")]


def _text(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y %H:%M:%S")
    text = str(value)
    for symbol in ("🥩", "🔥", "🌡", "♨", "🚫", "🟦", "🟩"):
        text = text.replace(symbol, "")
    return text.strip()


def _table(data, widths=None, header=True, font_size=7.5):
    wrapped = [[Paragraph(_text(cell), STYLES["Cell"]) for cell in row] for row in data]
    table = Table(wrapped, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
    ]
    if header:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, 0), DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ])
        if len(data) > 1:
            commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]))
    table.setStyle(TableStyle(commands))
    return table


def _chart(series, title, y_title="Temperature (C)"):
    drawing = Drawing(250 * mm, 92 * mm)
    chart = LinePlot()
    chart.x = 18 * mm
    chart.y = 14 * mm
    chart.width = 190 * mm
    chart.height = 60 * mm

    all_times = []
    all_values = []
    chart_data = []
    names = []
    for name, frame, time_col, value_col in series:
        clean = frame[[time_col, value_col]].dropna().sort_values(time_col).copy()
        if clean.empty:
            continue
        if len(clean) > 350:
            step = max(len(clean) // 350, 1)
            clean = clean.iloc[::step]
        start = clean[time_col].min()
        hours = (clean[time_col] - start).dt.total_seconds() / 3600.0
        values = pd.to_numeric(clean[value_col], errors="coerce")
        points = [(float(x), float(y)) for x, y in zip(hours, values) if pd.notna(y)]
        if points:
            chart_data.append(points)
            names.append(_text(name))
            all_times.extend(x for x, _ in points)
            all_values.extend(y for _, y in points)

    if not chart_data:
        return Paragraph("No chart data available.", STYLES["BodyText"])

    chart.data = chart_data
    chart.xValueAxis.valueMin = min(all_times)
    chart.xValueAxis.valueMax = max(all_times) if max(all_times) > min(all_times) else min(all_times) + 1
    chart.xValueAxis.valueSteps = None
    chart.xValueAxis.labelTextFormat = "%0.1f"
    chart.xValueAxis.labels.fontSize = 7
    chart.yValueAxis.valueMin = min(all_values) - 2
    chart.yValueAxis.valueMax = max(all_values) + 2
    chart.yValueAxis.labels.fontSize = 7
    for index in range(len(chart_data)):
        chart.lines[index].strokeColor = PALETTE[index % len(PALETTE)]
        chart.lines[index].strokeWidth = 1.3
        chart.lines[index].symbol = makeMarker("FilledCircle")
        chart.lines[index].symbol.size = 2

    legend = Legend()
    legend.x = 212 * mm
    legend.y = 72 * mm
    legend.fontSize = 7
    legend.colorNamePairs = [(PALETTE[i % len(PALETTE)], names[i]) for i in range(len(names))]
    drawing.add(chart)
    drawing.add(legend)
    drawing.add(String(18 * mm, 80 * mm, _text(title), fontName="Helvetica-Bold", fontSize=11, fillColor=DARK))
    drawing.add(String(18 * mm, 5 * mm, "Elapsed time from each stream start (hours)", fontSize=7, fillColor=DARK))
    drawing.add(String(2 * mm, 42 * mm, y_title, fontSize=7, fillColor=DARK))
    return drawing


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(DARK)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(15 * mm, 8 * mm, "Brisket Session Analyser - analytical report")
    canvas.drawRightString(282 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


STYLES = getSampleStyleSheet()
STYLES.add(ParagraphStyle(name="ReportTitle", parent=STYLES["Title"], fontName="Helvetica-Bold", fontSize=23, textColor=DARK, leading=27, spaceAfter=12))
STYLES.add(ParagraphStyle(name="Section", parent=STYLES["Heading2"], fontName="Helvetica-Bold", fontSize=15, textColor=ACCENT, leading=18, spaceBefore=10, spaceAfter=7))
STYLES.add(ParagraphStyle(name="Subsection", parent=STYLES["Heading3"], fontName="Helvetica-Bold", fontSize=11, textColor=DARK, leading=14, spaceBefore=8, spaceAfter=5))
STYLES.add(ParagraphStyle(name="Cell", parent=STYLES["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9))
STYLES.add(ParagraphStyle(name="Small", parent=STYLES["BodyText"], fontSize=7.5, leading=10, textColor=colors.HexColor("#4D5966")))
STYLES.add(ParagraphStyle(name="Centre", parent=STYLES["BodyText"], alignment=TA_CENTER))


def build_pdf_report(app_version, configuration, meat_results, stats, environment_results, transfer_time):
    """Return a complete analysis report as PDF bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Brisket Session Analyser {app_version} Report",
        author="Brisket Session Analyser",
    )
    story = []
    story.append(Paragraph(f"Brisket Session Analyser {app_version}", STYLES["ReportTitle"]))
    story.append(Paragraph("Full session analysis report", STYLES["Heading2"]))
    story.append(Paragraph(f"Generated: {datetime.now():%d/%m/%Y %H:%M:%S}", STYLES["Small"]))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Methodology", STYLES["Section"]))
    story.append(Paragraph("Tenderness and rendering calculations are based on brisket time-temperature and hot-hold concepts shared by Steve Gow. This application is an independent implementation and is not affiliated with or endorsed by Steve Gow. Results are analytical estimates and should be considered alongside probe tenderness and safe food handling.", STYLES["BodyText"]))

    story.append(Paragraph("Configuration", STYLES["Section"]))
    conf_data = [["Role", "Source"]] + [[r.get("Role"), r.get("Source")] for r in configuration]
    story.append(_table(conf_data, [95 * mm, 135 * mm]))
    story.append(Spacer(1, 4 * mm))
    boundary = _text(transfer_time) if transfer_time is not None else "Not detected"
    story.append(_table([["Derived smoker-to-hold boundary", boundary]], [80 * mm, 150 * mm], header=False))

    story.append(Paragraph("Session detection summary", STYLES["Section"]))
    rows = [["Profile", "Session", "Confidence", "Pull", "Peak C", "Average C", "Minimum C", "Cook h", "Hold h", "Average cook C", "Average hold C", "Sampling", "Gap threshold"]]
    for label, item in meat_results.items():
        result = item["result"]
        stat = stats[label]
        sample = item["sample"]
        rows.append([
            label, result.detection.session_type, f"{result.detection.confidence}%", _text(result.detection.pull_timestamp),
            f"{result.detection.peak_temperature:.1f}", f"{result.detection.average_temperature:.1f}", f"{result.detection.minimum_temperature:.1f}",
            f"{stat['cook_h']:.2f}", f"{stat['hold_h']:.2f}", _text(None if stat['cook_avg'] is None else round(stat['cook_avg'], 1)),
            _text(None if stat['hold_avg'] is None else round(stat['hold_avg'], 1)),
            _interval(sample['normal']), _interval(sample['threshold']),
        ])
    story.append(_table(rows, [40*mm, 22*mm, 15*mm, 30*mm, 13*mm, 15*mm, 15*mm, 13*mm, 13*mm, 18*mm, 18*mm, 17*mm, 17*mm], font_size=6.5))

    story.append(PageBreak())
    story.append(Paragraph("Brisket probe comparison", STYLES["Section"]))
    comparison = [["Profile", "Source", "Cook contribution", "Hold contribution", "Recorded total", "Assessment", "Analysed hours"]]
    meat_series = []
    for label, item in meat_results.items():
        result = item["result"]
        comparison.append([label, item["source"], f"{result.cook:.1%}", f"{result.hold:.1%}", f"{result.total:.1%}", result.assessment if result.complete else "Partial session", f"{result.analysed_hours:.2f}"])
        meat_series.append((label, item["valid"], "timestamp", "temperature_c"))
    story.append(_table(comparison, [52*mm, 45*mm, 27*mm, 27*mm, 24*mm, 65*mm, 22*mm]))
    story.append(Spacer(1, 4 * mm))
    story.append(_chart(meat_series, "Brisket Point and Flat temperature profiles"))

    story.append(PageBreak())
    story.append(Paragraph("Environment analysis", STYLES["Section"]))
    if environment_results:
        env_rows = [["Environment stream", "Role", "Start", "End", "Duration h", "Average C", "Minimum C", "Maximum C", "Stability"]]
        env_series = []
        for label, result in environment_results.items():
            frame = result.timeline
            env_rows.append([label, result.role, _text(frame['timestamp'].min()), _text(frame['timestamp'].max()), f"{_duration(frame):.2f}", f"{result.average:.1f}", f"{result.minimum:.1f}", f"{result.maximum:.1f}", f"{result.stability_score:.0f}/100"])
            env_series.append((label, frame, "timestamp", "temperature_c"))
        story.append(_table(env_rows, [55*mm, 43*mm, 30*mm, 30*mm, 18*mm, 18*mm, 18*mm, 18*mm, 18*mm], font_size=6.8))
        story.append(Spacer(1, 4 * mm))
        story.append(_chart(env_series, "Cook and Hold Environment profiles"))
    else:
        story.append(Paragraph("No environment streams were included in this analysis.", STYLES["BodyText"]))

    for label, item in meat_results.items():
        result = item["result"]
        story.append(PageBreak())
        story.append(Paragraph(label, STYLES["Section"]))
        stat = stats[label]
        metrics = [
            ["Metric", "Value", "Metric", "Value"],
            ["Source", item["source"], "Session", result.detection.session_type],
            ["Confidence", f"{result.detection.confidence}%", "Detected pull", _text(result.detection.pull_timestamp)],
            ["Cook duration", f"{stat['cook_h']:.2f} h", "Hold duration", f"{stat['hold_h']:.2f} h"],
            ["Cook contribution", f"{result.cook:.1%}", "Hold contribution", f"{result.hold:.1%}"],
            ["Recorded total", f"{result.total:.1%}", "Assessment", result.assessment if result.complete else "Partial session"],
            ["Analysed hours", f"{result.analysed_hours:.3f}", "Excluded gap hours", f"{result.excluded_gap_hours:.3f}"],
            ["Below 60 C hours", f"{result.below_model_hours:.3f}", "Sampling mode", item['sample']['mode']],
        ]
        story.append(_table(metrics, [45*mm, 85*mm, 45*mm, 85*mm]))
        story.append(Spacer(1, 3 * mm))
        story.append(_chart([(label, item["valid"], "timestamp", "temperature_c")], f"Temperature profile - {label}"))
        bands = result.summary[result.summary["Duration hours"] > 0].copy()
        if not bands.empty:
            story.append(Paragraph("Rendering band calculation", STYLES["Subsection"]))
            band_rows = [["Phase", "Band", "Temperature range", "Duration hours", "Rate per hour", "Tenderness contribution"]]
            for _, row in bands.iterrows():
                band_rows.append([row["Phase"], row["Band"], row["Temperature range"], f"{row['Duration hours']:.3f}", f"{row['Rate per hour']:.1%}", f"{row['Tenderness contribution']:.1%}"])
            story.append(_table(band_rows, [30*mm, 15*mm, 55*mm, 30*mm, 30*mm, 40*mm]))

    story.append(PageBreak())
    story.append(Paragraph("Interpretation notes", STYLES["Section"]))
    story.append(Paragraph("Cook Environment streams are evaluated before the derived transfer boundary. Hold Environment streams are evaluated after it. Missing values are preserved and are not interpolated across the smoker-to-hold transfer. Stability and event indicators are application-defined analytical estimates and are not manufacturer-provided Weber metrics.", STYLES["BodyText"]))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buffer.getvalue()


def _interval(seconds):
    if seconds is None:
        return "Unknown"
    if seconds < 60:
        return f"{seconds:.0f} sec"
    if seconds < 3600:
        return f"{seconds/60:.1f} min"
    return f"{seconds/3600:.2f} h"


def _duration(frame):
    if frame is None or len(frame) < 2:
        return 0.0
    return max((frame['timestamp'].iloc[-1] - frame['timestamp'].iloc[0]).total_seconds()/3600, 0.0)
