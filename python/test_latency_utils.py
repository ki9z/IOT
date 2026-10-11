"""Run locally: python -m unittest test_latency_utils.py
No Atlas account or network required.
"""
import unittest
from datetime import datetime, timedelta, timezone

from latency_utils import build_write_metric, summarize_durations


class WriteLatencyTests(unittest.TestCase):
    def test_metric_has_monotonic_and_observed_fields(self):
        t = datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc)
        sample = {
            "time": t,
            "received_at": t + timedelta(milliseconds=100),
            "metadata": {"device_id": "esp32_01"},
            "sequence": 7,
        }
        metric = build_write_metric(
            sample, "mock_id", "iot/demo/telemetry",
            t + timedelta(milliseconds=112), 10.45678, 12.2467,
        )
        self.assertEqual(metric["metadata"]["device_id"], "esp32_01")
        self.assertEqual(metric["raw_id"], "mock_id")
        self.assertEqual(metric["mongodb_insert_ms"], 10.457)
        self.assertEqual(metric["collector_to_db_ms"], 12.247)
        self.assertEqual(metric["observed_sensor_to_store_ms"], 112.0)

    def test_negative_monotonic_duration_is_invalid(self):
        t = datetime.now(timezone.utc)
        sample = {
            "time": t, "received_at": t,
            "metadata": {"device_id": "a"}, "sequence": 1,
        }
        with self.assertRaises(ValueError):
            build_write_metric(sample, "id", "topic", t, -1, 5)

    def test_summary_ignores_invalid_and_missing(self):
        result = summarize_durations([
            {"mongodb_insert_ms": 3.0, "collector_to_db_ms": 5.0},
            {"mongodb_insert_ms": 9.0, "collector_to_db_ms": 11.0},
            {"mongodb_insert_ms": None, "collector_to_db_ms": -1},
        ])
        self.assertEqual(result["measurements"], 3)
        self.assertEqual(result["median_mongodb_insert_ms"], 6.0)
        self.assertEqual(result["median_collector_to_db_ms"], 8.0)


if __name__ == "__main__":
    unittest.main()
