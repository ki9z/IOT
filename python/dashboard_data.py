"""Bounded MongoDB reads and explicit offline fixtures for the dashboard."""
from datetime import datetime, timedelta, timezone

import numpy as np
from pymongo import DESCENDING

from preprocess_iot import test_samples, to_docs_for_device

MAX_ROWS = 12000
RAW_FIELDS = {"_id": 0, "time": 1, "received_at": 1, "metadata": 1,
              "sequence": 1, "uptime_s": 1, "temperature": 1,
              "humidity": 1, "distance_cm": 1, "rssi": 1, "latency_ms": 1}


def read_snapshot(db, raw_name, processed_name, metrics_name, device,
                  minutes, window_seconds, anchor_history=False, now=None):
    """All collections share exactly one UTC time range; never mix old metrics.

    Live range ends at the server clock. Historical range ends at newest sample.
    Resampled buckets intersecting that range start at floor(start/window).
    """
    now = now or datetime.now(timezone.utc)
    device_query = {"metadata.device_id": device}
    newest = db[raw_name].find_one(device_query, {"time": 1}, sort=[("time", DESCENDING)])
    end = newest["time"] if anchor_history and newest else now
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(minutes=minutes)
    query = {**device_query, "time": {"$gte": start, "$lte": end}}
    rows = list(db[raw_name].find(query, RAW_FIELDS).sort("time", DESCENDING).limit(MAX_ROWS+1))
    truncated = len(rows) > MAX_ROWS
    rows = list(reversed(rows[:MAX_ROWS]))
    epoch = start.timestamp()
    bucket_start = datetime.fromtimestamp(epoch - epoch % window_seconds, timezone.utc)
    proc_query = {**device_query, "window_seconds": window_seconds,
                  "time": {"$gte": bucket_start, "$lte": end}}
    processed = list(db[processed_name].find(proc_query, {"_id": 0})
                     .sort("time", DESCENDING).limit(MAX_ROWS+1))
    proc_truncated = len(processed) > MAX_ROWS
    processed = list(reversed(processed[:MAX_ROWS]))
    # Filter by the associated sensor_time, not newest metric (which may be old).
    metric_query = {**device_query, "sensor_time": {"$gte": start, "$lte": end}}
    metrics_error = None
    try:
        metrics = list(db[metrics_name].find(metric_query, {"_id": 0, "raw_id": 0})
                       .sort("stored_at", DESCENDING).limit(MAX_ROWS+1))
    except Exception as exc:
        metrics, metrics_error = [], type(exc).__name__
    metrics_truncated = len(metrics) > MAX_ROWS
    metrics = metrics[:MAX_ROWS]
    return dict(raw=rows, processed=processed, metrics=metrics, start=start, end=end,
                truncated=truncated, processed_truncated=proc_truncated,
                metrics_truncated=metrics_truncated, metrics_error=metrics_error,
                latest_time=newest.get("time") if newest else None)


def demo_snapshot(minutes, window_seconds, now=None):
    """Synthetic measurements; never invoke MongoClient or write cloud data."""
    now = now or datetime.now(timezone.utc)
    source = test_samples()
    offset = now - timedelta(seconds=4) - max(row["time"] for row in source)
    rng = np.random.default_rng(42)
    for row in source:
        row["time"] += offset
        row["received_at"] = row["time"] + timedelta(milliseconds=180)
        row["metadata"] = {"device_id": "esp32_demo"}
        row["uptime_s"] = row["sequence"] * 5
        row["rssi"] = int(rng.integers(-89, -62))
        row["latency_ms"] = 180.0
    source.sort(key=lambda row: row["time"])
    start = now - timedelta(minutes=minutes)
    source = [row for row in source if start <= row["time"] <= now]
    docs, _ = to_docs_for_device(source, "esp32_demo", window_seconds)
    for doc in docs:
        doc["processed_at"] = now
    metrics = [{"stored_at": row["received_at"] + timedelta(milliseconds=23),
                "sensor_time": row["time"], "mongodb_insert_ms": float(rng.uniform(12, 35)),
                "collector_to_db_ms": float(rng.uniform(40, 65))} for row in source]
    return dict(raw=source, processed=docs, metrics=metrics, start=start, end=now,
                truncated=False, processed_truncated=False, metrics_truncated=False,
                metrics_error=None, latest_time=source[-1]["time"] if source else None)
