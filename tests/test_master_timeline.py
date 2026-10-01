from datetime import datetime, timedelta
import unittest

import pandas as pd

from master_timeline import (
    add_master_elapsed_time,
    align_streams_to_master,
    build_master_timeline,
)


class MasterTimelineTests(unittest.TestCase):
    def test_later_hold_stream_preserves_offset(self):
        cook_start = datetime(2026, 6, 1, 18, 0)
        hold_start = cook_start + timedelta(hours=10, minutes=15)

        cook = pd.DataFrame(
            {
                "timestamp": [cook_start, cook_start + timedelta(hours=10)],
                "temperature_c": [8.0, 92.0],
            }
        )
        hold = pd.DataFrame(
            {
                "timestamp": [hold_start, hold_start + timedelta(hours=2)],
                "temperature_c": [90.0, 82.0],
            }
        )

        master = build_master_timeline(
            [(cook, "timestamp"), (hold, "timestamp")],
            transfer_time=hold_start,
        )
        aligned = align_streams_to_master(
            {
                "Cook meat": (cook, "timestamp", "temperature_c"),
                "Hold meat": (hold, "timestamp", "temperature_c"),
            },
            master,
        )

        first_hold = aligned.loc[
            aligned["Stream"] == "Hold meat", "Elapsed session hours"
        ].min()
        self.assertAlmostEqual(first_hold, 10.25)
        self.assertAlmostEqual(master.transfer_elapsed_hours, 10.25)

    def test_raw_timestamp_is_preserved(self):
        start = datetime(2026, 6, 1, 18, 0)
        frame = pd.DataFrame(
            {"timestamp": [start, start + timedelta(hours=1)]}
        )
        master = build_master_timeline([(frame, "timestamp")])
        output = add_master_elapsed_time(frame, master)

        self.assertEqual(output.loc[0, "timestamp"], pd.Timestamp(start))
        self.assertEqual(output.loc[1, "Elapsed session hours"], 1.0)


if __name__ == "__main__":
    unittest.main()
