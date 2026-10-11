"""Regression checks for data loss, long outages and safe processed writes."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from preprocess_iot import save_processed, test_samples, to_docs_for_device

START = datetime(2026, 10, 11, tzinfo=timezone.utc)


def rows_at(seconds):
    return [dict(time=START+timedelta(seconds=s), sequence=i+1,
                 temperature=20+i, humidity=50, distance_cm=100)
            for i, s in enumerate(seconds)]


class PreprocessingTests(unittest.TestCase):
    def test_demo_flags_duplicate_missing_spike(self):
        rows = test_samples()
        before = [r.copy() for r in rows]
        docs, stats = to_docs_for_device(rows, "a", 30)
        self.assertEqual(rows, before)
        self.assertEqual(stats["duplicates_dropped"], 1)
        self.assertEqual(stats["invalid_sensor_values"], 2)
        self.assertEqual(stats["outliers_by_sensor"]["temperature"], 1)
        self.assertEqual(sum(d["sample_count"] for d in docs), 86)
        self.assertEqual(sum(d["outlier_count"] for d in docs), 1)
        self.assertTrue(all(0 <= d["temperature_norm"] <= 1 for d in docs if d["temperature_norm"] is not None))

    def test_short_internal_gap_fills_both_buckets(self):
        docs, stats = to_docs_for_device(rows_at([0, 90]), "a", 30)
        self.assertEqual([d["temperature"] for d in docs], [20, 20+1/3, 20+2/3, 21])
        self.assertEqual(stats["filled_by_sensor"]["temperature"], 2)
        self.assertTrue(docs[1]["was_missing_window"])
        self.assertTrue(docs[1]["imputed_by_sensor"]["temperature"])

    def test_long_gap_remains_entirely_missing(self):
        docs, stats = to_docs_for_device(rows_at([0, 150]), "a", 30)
        self.assertEqual(stats["filled_by_sensor"]["temperature"], 0)
        for d in docs[1:5]:
            self.assertIsNone(d["temperature"])
            self.assertIsNone(d["temperature_rolling_mean_3"])
            self.assertIsNone(d["temperature_norm"])
        self.assertIsNone(docs[-1]["temperature_delta"])

    def test_no_fill_at_edges_and_all_missing(self):
        source = rows_at([0, 30, 60])
        source[0]["humidity"] = None
        source[-1]["humidity"] = None
        for row in source:
            row["distance_cm"] = float("inf")
        docs, _ = to_docs_for_device(source, "a", 30)
        self.assertIsNone(docs[0]["humidity"])
        self.assertIsNone(docs[-1]["humidity"])
        self.assertTrue(all(d["distance_cm"] is None for d in docs))

    def test_bad_timestamp_and_constant_sensor(self):
        source = rows_at([0, 30]) + [dict(time="bad", temperature=50)]
        docs, stats = to_docs_for_device(source, "a", 30)
        self.assertEqual(stats["bad_times"], 1)
        self.assertTrue(all(d["humidity_norm"] == 0 for d in docs))

    def test_repeated_sequence_after_reboot_is_kept(self):
        source = rows_at([0, 30])
        source[1]["sequence"] = 1
        docs, stats = to_docs_for_device(source, "a", 30)
        self.assertEqual(stats["duplicates_dropped"], 0)
        self.assertEqual(sum(d["sample_count"] for d in docs), 2)

    def test_rejects_huge_ranges_before_resampling(self):
        with self.assertRaises(ValueError):
            to_docs_for_device(rows_at([0, 30*20000]), "a", 30)
        with self.assertRaises(ValueError):
            to_docs_for_device(rows_at([0]), "a", 0)

    def test_source_collection_collision_never_writes(self):
        db = MagicMock()
        docs, _ = to_docs_for_device(rows_at([0]), "a", 30)
        for name in ("iot_raw", "iot_write_metrics"):
            with self.assertRaises(ValueError):
                save_processed(db, docs, processed_name=name)
        db.__getitem__.assert_not_called()

    def test_timeseries_output_never_writes(self):
        db = MagicMock()
        db.list_collections.return_value = iter([{"options": {"timeseries": {"timeField": "time"}}}])
        docs, _ = to_docs_for_device(rows_at([0]), "a", 30)
        with self.assertRaises(ValueError):
            save_processed(db, docs)
        db.__getitem__.assert_not_called()

    def test_upserts_have_stable_keys(self):
        db = MagicMock()
        db.list_collections.return_value = iter([])
        coll = db.__getitem__.return_value
        coll.bulk_write.return_value = MagicMock(upserted_count=1, modified_count=0, matched_count=0)
        docs, _ = to_docs_for_device(rows_at([0]), "a", 30)
        result = save_processed(db, docs)
        self.assertEqual(result["upserted"], 1)
        self.assertEqual(db.__getitem__.call_args.args, ("iot_processed",))
        op = coll.bulk_write.call_args.args[0][0]
        self.assertEqual(op._filter, {"metadata.device_id": "a", "time": START, "window_seconds": 30})
        self.assertTrue(op._upsert)


if __name__ == "__main__":
    unittest.main()
