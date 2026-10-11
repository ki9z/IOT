import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
from dashboard_data import read_snapshot, demo_snapshot, MAX_ROWS
from dashboard_metrics import freshness, numeric_summary, quality_table, make_frame, sequence_audit

NOW = datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc)


def database(raw_rows=()):
    db = MagicMock()
    collections = {}
    for name in ("raw", "proc", "metrics"):
        coll = MagicMock()
        coll.find.return_value.sort.return_value.limit.return_value = list(raw_rows) if name == "raw" else []
        collections[name] = coll
    collections["raw"].find_one.return_value = {"time": NOW-timedelta(hours=4)}
    db.__getitem__.side_effect = collections.__getitem__
    return db, collections


class SnapshotTests(unittest.TestCase):
    def test_live_uses_now_and_same_metrics_range(self):
        db, cols = database()
        data = read_snapshot(db, "raw", "proc", "metrics", "a", 15, 30, now=NOW)
        self.assertEqual(data["end"], NOW)
        self.assertEqual(cols["raw"].find.call_args.args[0]["time"],
                         cols["metrics"].find.call_args.args[0]["sensor_time"])
        self.assertEqual(cols["proc"].find.call_args.args[0]["window_seconds"], 30)

    def test_history_uses_latest_sample(self):
        db, _ = database()
        data = read_snapshot(db, "raw", "proc", "metrics", "a", 15, 30, True, NOW)
        self.assertEqual(data["end"], NOW-timedelta(hours=4))

    def test_truncation_keeps_latest_and_reverses(self):
        db, _ = database([{"sequence": i} for i in range(MAX_ROWS+1)])
        data = read_snapshot(db, "raw", "proc", "metrics", "a", 15, 30, now=NOW)
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["raw"]), MAX_ROWS)
        self.assertEqual(data["raw"][-1]["sequence"], 0)

    def test_missing_metrics_permission_does_not_hide_raw(self):
        db, cols = database([{"time": NOW}])
        cols["metrics"].find.side_effect = PermissionError("sensitive URI must not surface")
        data = read_snapshot(db, "raw", "proc", "metrics", "a", 15, 30, now=NOW)
        self.assertEqual(data["metrics_error"], "PermissionError")
        self.assertEqual(len(data["raw"]), 1)

    def test_offline_demo_reproducible_and_labelled(self):
        a, b = demo_snapshot(15, 30, NOW), demo_snapshot(15, 30, NOW)
        self.assertEqual(a, b)
        self.assertEqual(a["raw"][0]["metadata"]["device_id"], "esp32_demo")

    def test_freshness_uses_reception_age(self):
        self.assertEqual(freshness(NOW-timedelta(seconds=10), NOW)[0], "Đang nhận dữ liệu")
        self.assertEqual(freshness(NOW-timedelta(minutes=2), NOW)[0], "Dữ liệu đã cũ")
        self.assertEqual(freshness(None, NOW), ("Không xác định", None))
        self.assertEqual(freshness(NOW+timedelta(seconds=10), NOW)[0], "Kiểm tra đồng hồ máy")

    def test_summary_ignores_nonfinite_and_preserves_empty(self):
        stats = numeric_summary([None, float("inf"), -1, "bad", 10, 20])
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["mean"], 15)
        self.assertEqual(stats["p95"], 19.5)
        self.assertIsNone(numeric_summary([])["mean"])

    def test_missing_columns_and_invalid_readings(self):
        raw = make_frame([dict(time=NOW, temperature=999, humidity=None, rssi=float("inf"))])
        q = quality_table(raw).set_index("Cảm biến")
        self.assertEqual(q.loc["temperature", "Ngoài miền"], 1)
        self.assertEqual(q.loc["distance_cm", "Thiếu"], 1)
        self.assertEqual(q.loc["rssi", "Thiếu"], 1)

    def test_audit_uses_receive_order_when_sensor_clock_reverses(self):
        raw = make_frame([dict(time=NOW, received_at=NOW, sequence=10, uptime_s=50),
                          dict(time=NOW-timedelta(minutes=1), received_at=NOW+timedelta(seconds=5), sequence=1, uptime_s=5)])
        self.assertEqual(sequence_audit(raw)["reboot_hints"], 1)
        self.assertEqual(sequence_audit(raw)["suspected_missing"], 0)


if __name__ == "__main__":
    unittest.main()
