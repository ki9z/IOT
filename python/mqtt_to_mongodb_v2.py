"""IoT Lab2 B3 - improved single-process MQTT collector.

Keep mqtt_to_mongodb.py and .env in the same folder. This version reuses the
payload validation from B2, checks MongoDB for repeated (device, time, seq)
before insert, and reports sequence gaps. It is not atomic deduplication and
must run as the ONLY subscriber that writes to iot_raw.
"""

import json
import os
import time
from datetime import datetime, timezone

from latency_utils import build_write_metric
import paho.mqtt.client as mqtt
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

# Reuse exactly the tested .env, input schema, and connection from Step 2.
import mqtt_to_mongodb as base

collection = None
write_metrics_collection = None
METRICS_NAME = os.getenv("MONGO_WRITE_METRICS_COLLECTION", "iot_write_metrics")


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("[MQTT] Connected; subscribing:", base.TOPIC)
        client.subscribe(base.TOPIC, qos=0)
    else:
        print("[MQTT ERROR] Connection code:", reason_code)


def on_message(client, userdata, msg):
    callback_started = time.perf_counter_ns()
    try:
        d = base.validate_payload(msg.payload)
        device = d["metadata"]["device_id"]
        seq = d["sequence"]
        sensor_time = d["time"]

        # Application-level deduplication across process restarts.
        # MongoDB time-series does not support unique indexes.
        duplicate_filter = {
            "metadata.device_id": device,
            "sequence": seq,
            "time": sensor_time,
        }
        if collection.find_one(duplicate_filter, {"_id": 1}):
            print(f"[DUPLICATE SKIPPED] device={device}, seq={seq}, time={sensor_time.isoformat()}")
            return

        # Compare with latest measurement from this device earlier in time.
        previous = collection.find_one(
            {"metadata.device_id": device, "time": {"$lt": sensor_time}},
            {"sequence": 1, "uptime_s": 1, "time": 1, "_id": 0},
            sort=[("time", DESCENDING)],
        )
        if previous and isinstance(previous.get("sequence"), int):
            prev_seq = previous["sequence"]
            prev_uptime = previous.get("uptime_s")
            uptime = d["uptime_s"]
            if isinstance(prev_uptime, int) and uptime < prev_uptime:
                print(f"[REBOOT POSSIBLE] uptime_s {prev_uptime} -> {uptime}; sequence reset can be normal")
            elif seq > prev_seq + 1:
                print(f"[GAP WARNING] {device}: sequence {prev_seq} -> {seq}; expected {prev_seq+1}")
            elif seq < prev_seq:
                print(f"[ORDER WARNING] sequence decreased {prev_seq} -> {seq}; check reboot/out-of-order data")

        d["source_topic"] = msg.topic
        write_started = time.perf_counter_ns()
        result = collection.insert_one(d)
        write_ended = time.perf_counter_ns()
        stored_at = datetime.now(timezone.utc)

        # Both timings below come from the SAME monotonic Python clock.
        # They exclude sensor-side / MQTT transport time and clock skew.
        insert_ms = (write_ended - write_started) / 1_000_000
        collector_ms = (write_ended - callback_started) / 1_000_000

        metric = build_write_metric(
            d, result.inserted_id, msg.topic, stored_at,
            insert_ms, collector_ms,
        )
        try:
            write_metrics_collection.insert_one(metric)
        except PyMongoError as exc:
            # Raw sample is saved even if diagnostic metrics fail.
            print(f"[METRICS WARNING] Raw sample saved, metric not saved: {type(exc).__name__}")

        print(f"[SAVED] seq={seq}, T={d['temperature']}C, H={d['humidity']}%, "
              f"distance={d['distance_cm']}cm, "
              f"db_write={insert_ms:.2f}ms, collector_to_db={collector_ms:.2f}ms, "
              f"id={result.inserted_id}")
    except (ValueError, UnicodeError, OverflowError, json.JSONDecodeError) as exc:
        print("[INVALID]", exc)
    except PyMongoError as exc:
        print("[MONGO ERROR]", type(exc).__name__, "-", str(exc).splitlines()[0][:200])


def main():
    global collection, write_metrics_collection
    base.mongo_client.admin.command("ping")
    collection = base.ensure_collection()
    write_metrics_collection = base.mongo_client[base.DB_NAME][METRICS_NAME]
    try:
        # A separate ordinary collection avoids modifying Time-Series samples.
        write_metrics_collection.create_index("stored_at", expireAfterSeconds=604800)
    except PyMongoError as exc:
        print(f"[METRICS WARNING] Unable to ensure metrics TTL: {type(exc).__name__}")
    print("[MONGO] Connected to Atlas")
    print(f"[METRICS] Writing acknowledged timings into {base.DB_NAME}.{METRICS_NAME}")
    print("[INFO] Run only ONE MQTT->MongoDB collector at a time")
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(base.BROKER, base.PORT, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        client.disconnect()
        base.mongo_client.close()


if __name__ == "__main__":
    try:
        main()
    except (PyMongoError, RuntimeError, OSError) as exc:
        print("[ERROR]", type(exc).__name__, "- check .env and Atlas connection")
        raise SystemExit(1)
