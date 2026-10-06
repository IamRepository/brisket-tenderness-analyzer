"""Tests for the Brisket Tenderness Analyzer engines.

Run from the repository folder:  python -m unittest test_analyzer
"""
import io
import unittest
from datetime import timedelta

import numpy as np
import pandas as pd

import brisket_engine as engine
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


if __name__ == "__main__":
    unittest.main()
