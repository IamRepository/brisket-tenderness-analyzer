"""Tests for the Brisket Tenderness Analyzer engines.

Run from the repository folder:  python -m unittest test_analyzer
"""
import io
import unittest
from datetime import timedelta

import numpy as np
import pandas as pd

import brisket_engine as engine
import labels
import pdf_report
import pit_engine as pit

T0 = pd.Timestamp("2026-01-01 08:00")


def stream(values, step_s=60, offset_s=0, start=T0):
    return pd.DataFrame({
        "timestamp": [start + timedelta(seconds=offset_s + step_s * i) for i in range(len(values))],
        "temperature_c": np.asarray(values, dtype=float),
    })


def profile(temps, step="15min"):
    timestamps = pd.date_range("2026-06-01 18:00", periods=len(temps), freq=step)
    return pd.DataFrame({"timestamp": timestamps, "temperature_c": np.asarray(temps, dtype=float)})


class Parsing(unittest.TestCase):
    def test_temperature_formats(self):
        parsed = engine.parse_temp(pd.Series(["23,5", "91.2", " 88,0 °C", "1.023,5", "1,023.5", "", "n/a", "65,25Celsius"])).tolist()
        self.assertEqual(parsed[:5], [23.5, 91.2, 88.0, 1023.5, 1023.5])
        self.assertTrue(pd.isna(parsed[5]) and pd.isna(parsed[6]))
        self.assertEqual(parsed[7], 65.25)

    def test_semicolon_csv_with_comma_decimals(self):
        text = "Timestamp;Point;Flat\n" + "".join(f"01/01/2026 08:{m:02d}:00;{60 + m},5;{58 + m},25\n" for m in range(10))
        frame = engine.read_file(io.BytesIO(text.encode()), "cook.csv")["CSV"]
        valid, report = engine.prepare(frame, "Timestamp", "Point")
        self.assertEqual(report.valid_rows, 10)
        self.assertEqual(valid.temperature_c.iloc[0], 60.5)


class Timestamps(unittest.TestCase):
    TZ = "Europe/Berlin"

    def wall(self, values, **kw):
        parsed, notes = engine.parse_timestamps(pd.Series(values, **kw), self.TZ)
        return [x.strftime("%Y-%m-%d %H:%M %Z") for x in parsed], notes

    def test_iso_dates_are_year_month_day(self):
        out, _ = self.wall(["2026-06-01 18:00:00", "2026-06-02 03:30:00", "2026-06-13 03:30:00"])
        self.assertEqual(out, ["2026-06-01 18:00 CEST", "2026-06-02 03:30 CEST", "2026-06-13 03:30 CEST"])

    def test_offsets_and_z_are_converted_not_crashing(self):
        out, notes = self.wall(["2026-06-01T16:00:00Z", "2026-06-01T18:30:00+02:00"])
        self.assertEqual(out, ["2026-06-01 18:00 CEST", "2026-06-01 18:30 CEST"])
        self.assertTrue(any("time zone" in n for n in notes))

    def test_zone_names_resolve_the_repeated_hour(self):
        out, _ = self.wall(["25/10/2026 02:30:00 CEST", "25/10/2026 02:10:00 CET", "01.06.2026 18:00 MESZ"])
        self.assertEqual(out, ["2026-10-25 02:30 CEST", "2026-10-25 02:10 CET", "2026-06-01 18:00 CEST"])

    def test_day_first_and_dotted_dates(self):
        out, _ = self.wall(["01.06.2026 18:00:00", "13/06/2026 03:30:00"])
        self.assertEqual(out, ["2026-06-01 18:00 CEST", "2026-06-13 03:30 CEST"])

    def test_month_first_when_proven_or_more_plausible(self):
        proven, notes = self.wall(["06/01/2026 18:00", "06/13/2026 03:30"])
        self.assertEqual(proven, ["2026-06-01 18:00 CEST", "2026-06-13 03:30 CEST"])
        self.assertIn("Dates read as month/day/year.", notes)
        # Both readings are possible; 1 June -> 2 June (a day) beats 6 Jan -> 6 Feb (a month).
        likely, _ = self.wall(["06/01/2026 06:00:00 PM", "06/02/2026 03:30:00 AM"])
        self.assertEqual(likely, ["2026-06-01 18:00 CEST", "2026-06-02 03:30 CEST"])

    def test_times_without_dates_roll_over_at_midnight(self):
        parsed, notes = engine.parse_timestamps(pd.Series(["22:00:00", "23:30:00", "00:15:00"]), self.TZ)
        self.assertEqual(parsed.diff().dt.total_seconds().iloc[1:].tolist(), [5400.0, 2700.0])
        self.assertTrue(notes)

    def test_spreadsheet_serials_and_native_datetimes(self):
        out, _ = self.wall([46174.75, 46175.145833])
        self.assertEqual(out, ["2026-06-01 18:00 CEST", "2026-06-02 03:30 CEST"])
        native, _ = self.wall(pd.to_datetime(["2026-06-01 18:00", "2026-06-02 03:30"]))
        self.assertEqual(native, ["2026-06-01 18:00 CEST", "2026-06-02 03:30 CEST"])

    def test_end_of_summer_time_keeps_every_reading(self):
        real = pd.date_range(pd.Timestamp("2026-10-25 00:00", tz=self.TZ), periods=240, freq="1min")
        local = real.tz_localize(None).strftime("%d/%m/%Y %H:%M:%S")  # wall clock repeats 02:00-03:00
        frame = pd.DataFrame({"Timestamp": local, "Point": np.linspace(80, 90, 240)})
        valid, report = engine.prepare(frame, "Timestamp", "Point", self.TZ)
        self.assertEqual((report.valid_rows, report.duplicate_timestamps), (240, 0))
        self.assertEqual((valid.timestamp.max() - valid.timestamp.min()).total_seconds(), 239 * 60)
        self.assertTrue(valid.timestamp.is_monotonic_increasing)

    def test_separate_date_and_time_columns_are_combined(self):
        t = pd.date_range("2026-06-01 22:00", periods=6, freq="30min")
        frame = pd.DataFrame({"Date": t.strftime("%d/%m/%Y"), "Time": t.strftime("%H:%M:%S"), "Point": range(60, 66)})
        sheets = engine.read_file(io.BytesIO(frame.to_csv(index=False).encode()), "cook.csv")
        merged = sheets["CSV"]
        timestamp, _ = engine.detect_columns(merged)
        self.assertEqual(timestamp, engine.COMBINED_TIME_COLUMN)
        parsed = engine.parse_ts(merged[timestamp], self.TZ)
        self.assertEqual(parsed.diff().dt.total_seconds().iloc[1:].unique().tolist(), [1800.0])

    def test_timezone_aware_probes_combine(self):
        a = stream([80.0] * 10); a["timestamp"] = a["timestamp"].dt.tz_localize(self.TZ)
        b = stream([90.0] * 10, offset_s=30); b["timestamp"] = b["timestamp"].dt.tz_localize(self.TZ)
        combined = engine.combine_probes([(a, 180), (b, 180)])
        self.assertTrue((combined.temperature_c.iloc[1:] == 85.0).all())


class Files(unittest.TestCase):
    def test_windows_encoded_csv(self):
        text = "Zeit;Fühler Point (°C)\n" + "".join(f"01.06.2026 18:{m:02d}:00;{60 + m},5\n" for m in range(10))
        frame = engine.read_file(io.BytesIO(text.encode("cp1252")), "cook.csv")["CSV"]
        self.assertIn("Fühler Point (°C)", frame.columns)
        valid, _ = engine.prepare(frame, "Zeit", "Fühler Point (°C)")
        self.assertEqual(len(valid), 10)

    def test_empty_file_has_a_plain_message(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            engine.read_file(io.BytesIO(b""), "cook.csv")

    def test_text_only_column_does_not_break_detection(self):
        frame = pd.DataFrame({"Timestamp": ["01/06/2026 18:00:00", "01/06/2026 18:01:00"], "Notes": ["start", "wrapped"]})
        with self.assertRaisesRegex(ValueError, "no temperature column"):
            engine.detect_columns(frame)


class ColumnRoles(unittest.TestCase):
    def test_suggested_roles(self):
        frame = pd.DataFrame({
            "timestamp": pd.date_range(T0, periods=100, freq="1min"),
            "Point": np.linspace(20, 70, 100), "Flat": np.linspace(20, 68, 100),
            "Grate Probe": 120 + np.tile([2, -2], 50), "Smoker Controller": [120] * 100,
            "Hold Environment": [75] * 100,
        })
        roles = pit.classify_columns(frame, "timestamp").set_index("Column")["Suggested role"].to_dict()
        self.assertEqual(roles["Point"], pit.POINT)
        self.assertEqual(roles["Flat"], pit.FLAT)
        self.assertEqual(roles["Grate Probe"], pit.COOK_GRATE)
        self.assertEqual(roles["Smoker Controller"], pit.COOK_PID)
        self.assertEqual(roles["Hold Environment"], pit.HOLD_ENV)

    def test_non_temperature_columns_are_ignored(self):
        frame = pd.DataFrame({
            "timestamp": pd.date_range(T0, periods=100, freq="1min"),
            "Setpoint": [115] * 100, "Target Temp": [115] * 100, "Fan %": np.tile([0, 60], 50),
            "Battery %": np.linspace(100, 60, 100), "Pit": 115 + np.tile([2, -2], 50),
        })
        roles = pit.classify_columns(frame, "timestamp").set_index("Column")["Suggested role"].to_dict()
        for name in ("Setpoint", "Target Temp", "Fan %", "Battery %"):
            self.assertEqual(roles[name], pit.IGNORE, name)
        self.assertEqual(roles["Pit"], pit.COOK_GRATE)


class PdfText(unittest.TestCase):
    def test_names_from_files_cannot_act_as_markup(self):
        from reportlab.platypus import Paragraph, SimpleDocTemplate
        import pdf_report
        for name in ["Probe 2 <b>", "Flat A</b>", "Point <deep>", "Flat & seam"]:
            cell = pdf_report._cell(f"Primary / {name}")
            SimpleDocTemplate(io.BytesIO()).build([Paragraph(cell, pdf_report.STYLES["Cell"])])
            self.assertIn("&lt;" if "<" in name else "&amp;", cell)


class CombineProbes(unittest.TestCase):
    def test_offset_probes_are_averaged_not_interleaved(self):
        combined = engine.combine_probes([(stream([80.0] * 10), 180), (stream([90.0] * 10, offset_s=30), 180)])
        self.assertTrue((combined.temperature_c.iloc[1:] == 85.0).all())
        self.assertEqual(combined.temperature_c.iloc[0], 80.0)

    def test_single_probe_is_unchanged(self):
        single = stream([70.0, 71.0, 72.5, 74.0, 75.0])
        combined = engine.combine_probes([(single, 180)])
        pd.testing.assert_series_equal(combined.temperature_c, single.temperature_c, check_names=False)

    def test_shared_gap_is_not_bridged(self):
        a = pd.concat([stream([80.0] * 5), stream([82.0] * 5, start=T0 + timedelta(hours=1))])
        b = pd.concat([stream([90.0] * 5, offset_s=30), stream([92.0] * 5, offset_s=30, start=T0 + timedelta(hours=1))])
        combined = engine.combine_probes([(a, 180), (b, 180)])
        self.assertGreater(combined.timestamp.diff().dt.total_seconds().max(), 3000)

    def test_probe_dropout_leaves_other_probe(self):
        a = stream([80.0] * 30)
        b = pd.concat([stream([90.0] * 5, offset_s=30), stream([90.0] * 5, offset_s=30, start=T0 + timedelta(minutes=20))])
        combined = engine.combine_probes([(a, 180), (b, 180)])
        window = combined[(combined.timestamp > T0 + timedelta(minutes=10)) & (combined.timestamp < T0 + timedelta(minutes=20))]
        self.assertTrue((window.temperature_c == 80.0).all())
        self.assertTrue((window.contributing_probes == 1).all())


class SessionAndRendering(unittest.TestCase):
    def test_cook_and_hold_detected(self):
        minutes = np.arange(0, 20 * 60)
        temps = np.interp(minutes / 60, [0, 2, 8, 10, 14, 20], [8, 45, 85, 95, 90, 70])
        self.assertEqual(engine.classify_session(stream(temps)).session_type, "Cook + Hold")

    def test_assessment_scale(self):
        self.assertEqual(engine.assess(0.79), "Underdone and tight")
        self.assertEqual(engine.assess(0.90), "Slightly tight but sliceable")
        self.assertEqual(engine.assess(1.00), "Ideal tenderness")
        self.assertEqual(engine.assess(1.10), "Very soft and potentially overdone")
        self.assertEqual(engine.assess(1.30), "Increased risk of mushy or over-rendered texture")

    def test_one_hour_in_a_band_adds_its_rate(self):
        frame = stream([92.0] * 61)  # one hour at 92 C (band 7, 29 % per hour)
        report = engine.ParseReport(61, 61, 0, 0, frame.timestamp.min(), frame.timestamp.max())
        detection = engine.Detection("Cook Only", 90, None, 92.0, 92.0, 92.0, "")
        result = engine.analyse(frame, report, detection, max_gap=180)
        self.assertAlmostEqual(result.total, 0.29, places=6)


def cook_and_rest(cook_h, rest_h, rest_end, step_min=1):
    start = pd.Timestamp("2026-06-01 18:00", tz="Europe/Berlin")
    t = start + pd.to_timedelta(np.arange(0, int((cook_h + rest_h) * 60) + 1, step_min), unit="min")
    h = (t - start).total_seconds() / 3600
    points, values = [0, 2, cook_h * 0.6, cook_h, cook_h + rest_h], [6, 40, 75, 94, rest_end]
    return pd.DataFrame({"timestamp": t, "temperature_c": np.interp(h, points, values)}), start + pd.Timedelta(hours=cook_h)


def report_for(frame):
    return engine.ParseReport(len(frame), len(frame), 0, 0, frame.timestamp.min(), frame.timestamp.max())


class ShortRests(unittest.TestCase):
    def test_one_hour_rest_is_a_cook_and_hold(self):
        for step in (1, 5, 15):
            frame, true_pull = cook_and_rest(12, 1, 80, step)
            detection = engine.classify_session(frame)
            self.assertEqual(detection.session_type, "Cook + Hold", f"{step}-min logging")
            self.assertLessEqual(abs((detection.pull_timestamp - true_pull).total_seconds()), step * 60 + 300)

    def test_cook_without_rest_stays_cook_only(self):
        frame, _ = cook_and_rest(10, 0.25, 93)
        self.assertEqual(engine.classify_session(frame).session_type, "Cook Only")


class RenderingCalculation(unittest.TestCase):
    def test_interval_by_interval(self):
        # 70 C for 60 s (band 2), 92 C for 120 s (band 7), a 10-min gap, 50 C for 60 s (below 60 C).
        t = pd.to_datetime(["2026-06-01 10:00:00", "2026-06-01 10:01:00", "2026-06-01 10:03:00",
                            "2026-06-01 10:13:00", "2026-06-01 10:14:00"]).tz_localize("Europe/Berlin")
        frame = pd.DataFrame({"timestamp": t, "temperature_c": [70.0, 92.0, 92.0, 50.0, 50.0]})
        detection = engine.Detection("Cook Only", 90, None, 92, 70, 50, "")
        result = engine.analyse(frame, report_for(frame), detection, max_gap=180)
        expected = 60 / 3600 * 0.0245 + 120 / 3600 * 0.29
        self.assertAlmostEqual(result.total, expected, places=12)
        self.assertEqual(result.timeline["Status"].tolist(), ["Analysed", "Analysed", "Excluded: recording gap", "Below model range"])
        self.assertEqual(result.excluded_gaps, 1)
        self.assertAlmostEqual(result.below_model_hours, 60 / 3600)

    def test_transfer_splits_an_interval(self):
        t = pd.to_datetime(["2026-06-01 10:00", "2026-06-01 10:02"]).tz_localize("Europe/Berlin")
        frame = pd.DataFrame({"timestamp": t, "temperature_c": [92.0, 92.0]})
        detection = engine.Detection("Cook + Hold", 95, None, 92, 92, 92, "")
        result = engine.analyse(frame, report_for(frame), detection, pull_override=t[0] + pd.Timedelta(seconds=30), max_gap=180)
        self.assertAlmostEqual(result.cook, 30 / 3600 * 0.29)
        self.assertAlmostEqual(result.hold, 90 / 3600 * 0.29)
        self.assertEqual(result.timeline["Phase"].iloc[0], "Cook → Hold / cooldown")

    def test_transfer_set_for_the_session_splits_any_profile(self):
        frame, _ = cook_and_rest(10, 0.25, 93)
        detection = engine.classify_session(frame)
        self.assertEqual(detection.session_type, "Cook Only")
        transfer = frame.timestamp.iloc[0] + pd.Timedelta(hours=8)
        result = engine.analyse(frame, report_for(frame), detection, pull_override=transfer, max_gap=180)
        self.assertGreater(result.hold, 0)
        self.assertTrue(result.complete)

    def test_long_logs_are_fast(self):
        import time
        frame, _ = cook_and_rest(20, 4, 70, step_min=1)
        frame = frame.set_index("timestamp").resample("2s").interpolate().reset_index()  # about 43,000 readings
        started = time.perf_counter()
        engine.analyse(frame, report_for(frame), engine.classify_session(frame), max_gap=6)
        self.assertLess(time.perf_counter() - started, 1.0)


class Stability(unittest.TestCase):
    def test_stepped_program_is_not_penalised(self):
        result = pit.analyse(profile([80] * 14 + [110] * 12 + [150] * 13), pit.COOK_PID)
        self.assertEqual(result.stability_score, 100.0)
        self.assertEqual(result.level_changes, 2)

    def test_stepped_program_with_ramps_and_noise(self):
        minutes = np.arange(570)
        program = np.interp(minutes, [0, 210, 225, 390, 410, 570], [80, 80, 110, 110, 150, 150])
        result = pit.analyse(profile(program + np.random.default_rng(1).normal(0, 1.5, 570), "1min"), pit.COOK_PID)
        self.assertGreater(result.stability_score, 75)
        self.assertEqual(result.level_changes, 2)

    def test_poor_control_still_scores_low(self):
        result = pit.analyse(profile(110 + np.random.default_rng(2).normal(0, 6, 570), "1min"), pit.COOK_PID)
        self.assertLess(result.stability_score, 40)

    def test_constant_hold_is_fully_stable(self):
        result = pit.analyse(profile([75.0] * 48), pit.HOLD_ENV)
        self.assertEqual(result.stability_score, 100.0)


class MixedTimeZones(unittest.TestCase):
    """A time column that mixes zoned and unzoned values is read value by value, never as one zone."""

    def test_minority_zoned(self):
        text = [f"2026-06-01 18:{m:02d}:00" for m in range(10)]
        text[3] = "2026-06-01 18:03:00+02:00"
        parsed, notes = engine.parse_timestamps(pd.Series(text), "Europe/Berlin")
        self.assertEqual(parsed.iloc[3], pd.Timestamp("2026-06-01 18:03", tz="Europe/Berlin"))
        self.assertEqual(parsed.iloc[4], pd.Timestamp("2026-06-01 18:04", tz="Europe/Berlin"))
        self.assertTrue(any("some do not" in note for note in notes))

    def test_majority_zoned_leaves_unzoned_in_local_time_not_utc(self):
        text = [f"2026-06-01 18:{m:02d}:00+02:00" for m in range(10)]
        text[3] = "2026-06-01 18:03:00"
        parsed, _ = engine.parse_timestamps(pd.Series(text), "Europe/Berlin")
        self.assertEqual(parsed.iloc[3], pd.Timestamp("2026-06-01 18:03", tz="Europe/Berlin"))
        self.assertEqual(parsed.iloc[2], pd.Timestamp("2026-06-01 18:02", tz="Europe/Berlin"))

    def test_zoned_value_is_converted_to_the_viewer_zone(self):
        text = ["13/06/2026 16:00:00Z"] + [f"13/06/2026 18:{m:02d}:00" for m in range(1, 10)]
        parsed, _ = engine.parse_timestamps(pd.Series(text), "Europe/Oslo")
        self.assertEqual(parsed.iloc[0], pd.Timestamp("2026-06-13 18:00", tz="Europe/Oslo"))

    def test_all_zoned_and_all_unzoned_are_unchanged(self):
        zoned, notes = engine.parse_timestamps(pd.Series([f"2026-06-01 16:0{m}:00Z" for m in range(5)]), "Europe/Berlin")
        self.assertEqual(zoned.iloc[0], pd.Timestamp("2026-06-01 18:00", tz="Europe/Berlin"))
        self.assertFalse(any("some do not" in note for note in notes))


class DetectColumns(unittest.TestCase):
    def hot_pit_file(self, blank_rows=0):
        times = pd.Series(pd.date_range("2026-06-13 06:00", periods=60, freq="1min").strftime("%d/%m/%Y %H:%M:%S"), dtype=object)
        values = pd.Series(165.0 + np.sin(np.arange(60)), dtype=object)
        times.iloc[:blank_rows] = np.nan
        values.iloc[:blank_rows] = np.nan
        return pd.DataFrame({"Time": times, "Smoker": values})

    def test_environment_only_file_is_accepted_when_probes_are_not_required(self):
        timestamp, probes = engine.detect_columns(self.hot_pit_file(), require_probes=False)
        self.assertEqual(timestamp, "Time")
        self.assertEqual(probes, [])

    def test_environment_only_file_still_raises_by_default(self):
        with self.assertRaises(ValueError):
            engine.detect_columns(self.hot_pit_file())

    def test_blank_leading_rows_do_not_hide_the_columns(self):
        frame = self.hot_pit_file(blank_rows=5)
        frame = frame.rename(columns={"Time": "Clock reading"})
        timestamp, _ = engine.detect_columns(frame, require_probes=False)
        self.assertEqual(timestamp, "Clock reading")
        point = pd.DataFrame({"Clock reading": frame["Clock reading"], "Point": [np.nan] * 5 + [60.0 + i for i in range(55)]})
        self.assertEqual(engine.detect_columns(point)[1], ["Point"])

    def test_empty_worksheet_gets_a_clear_message(self):
        for frame in (pd.DataFrame(), pd.DataFrame({"Timestamp": [np.nan], "Point": [np.nan]})):
            with self.assertRaisesRegex(ValueError, "worksheet is empty"):
                engine.detect_columns(frame)


class FileSize(unittest.TestCase):
    def test_a_file_above_the_row_limit_is_refused_with_a_message(self):
        text = "Timestamp,Point\n" + "".join(f"01/01/2026 08:{m:02d}:00,{60 + m}\n" for m in range(10))
        original = engine.MAX_ROWS
        engine.MAX_ROWS = 5
        try:
            with self.assertRaisesRegex(ValueError, "10 rows, more than the 5"):
                engine.read_file(io.BytesIO(text.encode()), "big.csv")
        finally:
            engine.MAX_ROWS = original
        self.assertEqual(len(engine.read_file(io.BytesIO(text.encode()), "ok.csv")["CSV"]), 10)


class Labels(unittest.TestCase):
    def test_markdown_in_file_names_is_neutralised(self):
        shown = labels.md_safe("[link](https://example.com) ![i](https://example.com/p.png) *bold* `x`")
        for character in "[]()*`!":
            self.assertNotIn(character, shown.replace("\\" + character, ""))
        self.assertNotIn("://", shown)  # no web address left for the renderer to turn into a link

    def test_addresses_and_emoji_codes_are_broken_up(self):
        for text in ("www.example.com", "WWW.example.com", "someone@example.com", ":smile:", ":material/timer:"):
            shown = labels.md_safe(text)
            self.assertIn("\u200b", shown)
            self.assertEqual(shown.replace("\u200b", ""), text)

    def test_ordinary_names_read_the_same(self):
        self.assertEqual(labels.md_safe("Hold probe"), "Hold probe")
        self.assertEqual(labels.md_safe("Hold probe 2 (top) 75.0"), "Hold probe 2 \\(top\\) 75.0")

    def test_plotly_names_cannot_carry_tags(self):
        self.assertEqual(labels.plotly_text('<a href="x">Hold & co</a>'), '&lt;a href="x"&gt;Hold &amp; co&lt;/a&gt;')

    def test_same_role_and_column_in_two_files_get_two_labels(self):
        taken = {}
        for file_label in ("Primary", "Secondary"):
            taken[labels.unique_label("Hold", file_label, "Hold probe", taken)] = 1
        self.assertEqual(len(taken), 2)

    def test_stream_names_tell_sensors_of_one_role_apart(self):
        names = labels.environment_stream_names({
            "a": ("Hold Environment", "Secondary", "Hold top"),
            "b": ("Hold Environment", "Secondary", "Hold bottom"),
            "c": ("Cook Environment - Grate", "Primary", "Grate Probe"),
        })
        self.assertEqual(names["a"], "Hold Environment (Hold top)")
        self.assertEqual(names["b"], "Hold Environment (Hold bottom)")
        self.assertEqual(names["c"], "Cook Environment - Grate")

    def test_stream_names_add_the_file_when_columns_repeat(self):
        names = labels.environment_stream_names({
            "a": ("Hold Environment", "Primary", "Hold probe"),
            "b": ("Hold Environment", "Secondary", "Hold probe"),
        })
        self.assertEqual(len(set(names.values())), 2)
        self.assertIn("Primary / Hold probe", names["a"])


class PdfTimes(unittest.TestCase):
    def test_negative_durations_keep_their_sign(self):
        self.assertEqual(pdf_report._hours(-1800), "-0 h 30 min")
        self.assertEqual(pdf_report._hours(5400), "1 h 30 min")
        self.assertEqual(pdf_report._hours(0), "0 h 00 min")
        self.assertEqual(pdf_report._hours(None), "N/A")

    def test_source_is_split_into_file_and_column(self):
        self.assertEqual(pdf_report._split_source("Secondary / Hold top"), ("Secondary", "Hold top"))
        self.assertEqual(pdf_report._split_source("Hold top"), ("", "Hold top"))
        self.assertEqual(pdf_report._split_source(None), ("", ""))


if __name__ == "__main__":
    unittest.main()
