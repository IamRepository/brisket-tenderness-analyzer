"""Tests that run the whole app (app.py) in Streamlit's test runner, with the file uploads faked.

Run from the repository folder:  python -m unittest test_app
"""
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # Streamlit is in requirements.txt; skip rather than fail where it is missing
    AppTest = None

HERE = os.path.dirname(os.path.abspath(__file__))

# Runs app.py with st.file_uploader replaced by files named in E2E_FILES, and (optionally) the second
# parse of a time column made to fail, which is how a secondary file can break after its columns are known.
WRAPPER = '''
import io, os, sys, json, pathlib
import streamlit as st
sys.path.insert(0, {here!r})
class Upload(io.BytesIO):
    def __init__(self, path, key):
        super().__init__(pathlib.Path(path).read_bytes())
        self.name = os.path.basename(path); self.file_id = key + ":" + self.name; self.size = len(self.getvalue())
_paths = json.loads(os.environ["E2E_FILES"])
def _uploader(self, label, *args, **kwargs):
    path = _paths.get(kwargs.get("key"))
    return Upload(path, kwargs["key"]) if path else None
from streamlit.delta_generator import DeltaGenerator
DeltaGenerator.file_uploader = _uploader
if os.environ.get("E2E_FAIL_SECOND_TIMES"):
    import brisket_engine as _engine
    _original, _calls = _engine.parse_timestamps, {{"n": 0}}
    def _failing(series, tz=_engine.LOCAL_TZ):
        _calls["n"] += 1
        if _calls["n"] == 2:
            raise ValueError("simulated failure")
        return _original(series, tz)
    _engine.parse_timestamps = _failing
exec(compile(pathlib.Path({app!r}).read_text(encoding="utf-8"), "app.py", "exec"))
'''.format(here=HERE, app=os.path.join(HERE, "app.py"))

HOLD = "♨ Hold Environment - Probe"


def stamps(start, count):
    return pd.date_range(start, periods=count, freq="1min").strftime("%d/%m/%Y %H:%M:%S")


def write_cook(folder, point_header="Point"):
    hours = np.arange(12 * 60) / 60
    pd.DataFrame({
        "Timestamp": stamps("2026-06-13 06:00", len(hours)),
        point_header: np.interp(hours, [0, 2, 8, 9.5, 12], [8, 40, 92, 95, 80]).round(1),
        "Flat": np.interp(hours, [0, 2, 8, 9.5, 12], [8, 42, 94, 96, 78]).round(1),
    }).to_csv(os.path.join(folder, "cook.csv"), index=False)


def write_hold(folder, name, columns):
    frame = pd.DataFrame({"Timestamp": stamps("2026-06-13 15:00", 180)})
    for column, level in columns.items():
        frame[column] = (level + np.random.default_rng(len(column)).normal(0, 0.3, 180)).round(1)
    frame.to_csv(os.path.join(folder, name), index=False)


@unittest.skipIf(AppTest is None, "Streamlit is not installed")
class WholeApp(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def path(self, name):
        return os.path.join(self.folder.name, name)

    def run_app(self, primary, secondary=None, roles=None, fail_second_times=False, analyse=True):
        files = {"primary": self.path(primary)}
        if secondary:
            files["secondary"] = self.path(secondary)
        os.environ["E2E_FILES"] = json.dumps(files)
        if fail_second_times:
            os.environ["E2E_FAIL_SECOND_TIMES"] = "1"
        else:
            os.environ.pop("E2E_FAIL_SECOND_TIMES", None)
        app = AppTest.from_string(WRAPPER, default_timeout=120)
        app.run()
        for box in app.selectbox:
            for start, role in (roles or {}).items():
                if box.label.startswith(start):
                    box.select(role)
        if roles:
            app.run()
        if analyse:
            next(b for b in app.button if b.label == "Analyse session").click()
            app.run()
        return app

    def test_secondary_file_that_fails_halfway_leaves_the_app_working(self):
        write_cook(self.folder.name)
        write_hold(self.folder.name, "hold.csv", {"Hold probe": 75})
        app = self.run_app("cook.csv", "hold.csv", {"Hold probe": HOLD}, fail_second_times=True)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("secondary file could not be read" in e.value for e in app.error))
        self.assertFalse(any(box.label.startswith("Hold probe") for box in app.selectbox))
        self.assertIn("Whole brisket tenderness assessment", [h.value for h in app.header])

    def test_two_hold_sensors_with_the_same_name_stay_two_streams(self):
        write_cook(self.folder.name)
        write_hold(self.folder.name, "hold_a.csv", {"Hold probe": 75})
        write_hold(self.folder.name, "hold_b.csv", {"Hold probe": 68})
        # Same role and column name in both files: put a hold probe in the primary file as well.
        primary = pd.read_csv(self.path("cook.csv"))
        primary["Hold probe"] = np.where(np.arange(len(primary)) > 9.5 * 60, 75.0, np.nan)
        primary.to_csv(self.path("primary.csv"), index=False)
        app = self.run_app("primary.csv", "hold_b.csv", {"Hold probe": HOLD})
        self.assertEqual(len(app.exception), 0)
        table = next(m.value for m in app.markdown if "Environment stream" in m.value)
        self.assertEqual(table.count("<tr>") - 1, 2)
        integrity = next(m.value for m in app.markdown if "Environment role" in m.value)
        self.assertIn("<td>2</td>", integrity.replace(' class="num"', ""))

    def test_two_hold_sensors_with_different_columns_are_two_chart_lines(self):
        write_cook(self.folder.name)
        write_hold(self.folder.name, "hold.csv", {"Hold top": 75, "Hold bottom": 66})
        app = self.run_app("cook.csv", "hold.csv", {"Hold top": HOLD, "Hold bottom": HOLD})
        self.assertEqual(len(app.exception), 0)
        names = []
        for chart in app.get("plotly_chart"):
            names += [trace.get("name") for trace in json.loads(chart.proto.spec)["data"]]
        self.assertIn("Hold Environment - Probe (Hold top)", names)
        self.assertIn("Hold Environment - Probe (Hold bottom)", names)

    def test_environment_only_secondary_file_with_a_hot_pit_is_accepted(self):
        write_cook(self.folder.name)
        pd.DataFrame({"Time": stamps("2026-06-13 06:00", 540), "Smoker": 160.0 + np.sin(np.arange(540))}).to_csv(self.path("smoker.csv"), index=False)
        app = self.run_app("cook.csv", "smoker.csv", analyse=False)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertTrue(any(box.label.startswith("Smoker") for box in app.selectbox))

    def test_markdown_in_a_column_header_is_shown_as_text(self):
        write_cook(self.folder.name, point_header="[link](https://evil.example)")
        app = self.run_app("cook.csv", analyse=False)
        labels = [box.label for box in app.selectbox]
        shown = next(label for label in labels if label.startswith("\\[link\\]"))
        self.assertNotIn("://", shown)  # not a web address the renderer could link
        self.assertNotIn("[link](", shown)


if __name__ == "__main__":
    unittest.main()
