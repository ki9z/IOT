"""Utilities for timing data ingestion without depending on MongoDB or MQTT.

A single Python monotonic clock can measure callback-to-ack and insert duration.
Sensor-to-store timestamp differences are observational only until clocks are
synchronized; never present them as verified network/end-to-end latency.
"""
from datetime import datetime, timezone
from statistics import median


def build_write_metric(sample, inserted_id, source_topic, stored_at,
                       mongodb_insert_ms, collector_to_db_ms):
    if not isinstance(stored_at, datetime) or stored_at.tzinfo is None:
        raise ValueError("stored_at must be timezone-aware")
    for value in (mongodb_insert_ms, collector_to_db_ms):
        if value < 0:
            raise ValueError("monotonic durations cannot be negative")
    stored_at = stored_at.astimezone(timezone.utc)
    sensor_time = sample["time"]
    if sensor_time.tzinfo is None:
        sensor_time = sensor_time.replace(tzinfo=timezone.utc)
    observed = (stored_at - sensor_time).total_seconds() * 1000
    return {
        "metadata": {"device_id": sample["metadata"]["device_id"]},
        "raw_id": str(inserted_id),
        "sequence": sample["sequence"],
        "sensor_time": sensor_time,
        "received_at": sample["received_at"],
        "stored_at": stored_at,
        "source_topic": source_topic,
        "mongodb_insert_ms": round(float(mongodb_insert_ms), 3),
        "collector_to_db_ms": round(float(collector_to_db_ms), 3),
        "observed_sensor_to_store_ms": round(observed, 3),
    }


def summarize_durations(rows):
    values_insert = [r["mongodb_insert_ms"] for r in rows
                     if isinstance(r.get("mongodb_insert_ms"), (int, float))
                     and r["mongodb_insert_ms"] >= 0]
    values_total = [r["collector_to_db_ms"] for r in rows
                    if isinstance(r.get("collector_to_db_ms"), (int, float))
                    and r["collector_to_db_ms"] >= 0]
    return {
        "measurements": len(rows),
        "median_mongodb_insert_ms": median(values_insert) if values_insert else None,
        "median_collector_to_db_ms": median(values_total) if values_total else None,
    }
