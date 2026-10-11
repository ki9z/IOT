"""IoT Lab2 B3 - Check MongoDB time-series schema/TTL and audit duplicate/missing samples.

Run from same folder as your existing .env:
  python step3_check.py
  python step3_check.py --set-retention
"""
import argparse
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "").strip()
DB_NAME = os.getenv("MONGO_DB", "iot_lab2")
COLL_NAME = os.getenv("MONGO_COLLECTION", "iot_raw")
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_DOCS = 10000  # Enough for class demonstration, not an exhaustive full history audit.


def main():
    parser = argparse.ArgumentParser(description="Check schema, TTL and data integrity of IoT time-series")
    parser.add_argument("--set-retention", action="store_true", help="Set collection TTL to 7 days (requires collMod permission)")
    args = parser.parse_args()

    if not MONGO_URI or "<" in MONGO_URI or ">" in MONGO_URI:
        raise SystemExit("[ERROR] Fill MONGO_URI in .env; do not share the file.")

    with MongoClient(MONGO_URI, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=12000) as client:
        client.admin.command("ping")
        db = client[DB_NAME]
        info = next(db.list_collections(filter={"name": COLL_NAME}), None)
        if info is None:
            raise SystemExit(f"[ERROR] Missing collection: {DB_NAME}.{COLL_NAME}")

        options = info.get("options", {})
        ts = options.get("timeseries")
        print("\n=== 1. COLLECTION CONFIGURATION ===")
        print(f"Database: {DB_NAME}")
        print(f"Collection: {COLL_NAME}")
        print(f"Time-series: {'YES' if ts else 'NO'}")
        if not ts:
            raise SystemExit("[ERROR] This collection is not time-series. Do not recreate or delete it without backup.")
        print("timeField:", ts.get("timeField"))
        print("metaField:", ts.get("metaField", "(not set)"))
        print("granularity:", ts.get("granularity", "(server default)"))
        if ts.get("timeField") != "time" or ts.get("metaField") != "metadata":
            print("[WARNING] Your field names differ from the sample collector.")

        current_ttl = options.get("expireAfterSeconds")
        print("expireAfterSeconds:", current_ttl if current_ttl is not None else "NOT SET")

        if args.set_retention:
            print("\nApplying a 7-day retention policy...")
            try:
                db.command({"collMod": COLL_NAME, "expireAfterSeconds": RETENTION_SECONDS})
                refreshed = next(db.list_collections(filter={"name": COLL_NAME}), {})
                actual = refreshed.get("options", {}).get("expireAfterSeconds")
                print("[OK] New expireAfterSeconds:", actual)
            except PyMongoError as exc:
                print("[ERROR] Cannot modify retention:", type(exc).__name__)
                print("This may require a database role with the collMod privilege (e.g. dbAdmin).")
                print("Existing documents are unchanged. Ask the administrator or change privileges in Atlas.")
        elif current_ttl != RETENTION_SECONDS:
            print("[ACTION] To set TTL to 7 days, run: python step3_check.py --set-retention")

        print("\n=== 2. DATA AUDIT ===")
        col = db[COLL_NAME]
        count = col.count_documents({})
        print("Total documents:", count)
        if count == 0:
            print("[INFO] No data yet. Start Wokwi and the MQTT collector, then re-run.")
            return

        projection = {"time": 1, "metadata.device_id": 1, "sequence": 1,
                      "uptime_s": 1, "temperature": 1, "humidity": 1,
                      "distance_cm": 1, "latency_ms": 1}
        # For class-sized datasets, scan up to MAX_DOCS in ascending sensor time.
        docs = list(col.find({}, projection).sort("time", ASCENDING).limit(MAX_DOCS))
        if count > MAX_DOCS:
            print(f"[WARNING] Auditing first {MAX_DOCS} records only out of {count}.")
        by_device = defaultdict(list)
        invalid = 0
        for d in docs:
            device = d.get("metadata", {}).get("device_id")
            if not device or not isinstance(d.get("time"), datetime) or not isinstance(d.get("sequence"), int):
                invalid += 1
                continue
            by_device[device].append(d)

        print("Invalid core fields:", invalid)
        for device, items in by_device.items():
            seen = set()
            duplicates = []
            gaps = []
            restarts = 0
            prev = None
            latencies = []
            for d in items:
                seq = d["sequence"]
                t = d["time"]
                uptime = d.get("uptime_s")
                key = (seq, t)
                if key in seen:
                    duplicates.append((seq, t))
                    continue
                seen.add(key)
                if isinstance(d.get("latency_ms"), (int, float)):
                    latencies.append(d["latency_ms"])
                if prev is not None:
                    ps, pt, pu = prev
                    if isinstance(uptime, int) and isinstance(pu, int) and uptime < pu:
                        restarts += 1
                    elif seq > ps + 1 and t > pt and (not isinstance(uptime, int) or not isinstance(pu, int) or uptime >= pu):
                        gaps.append((ps + 1, seq - 1))
                    elif seq < ps:
                        print(f"[NOTE] {device}: sequence decreased {ps}->{seq}; possible reboot or out-of-order data")
                prev = (seq, t, uptime)

            total_missing = sum(b - a + 1 for a, b in gaps)
            print(f"\nDevice: {device}")
            print("  Records checked:", len(items))
            print("  Repeated (sequence, time):", len(duplicates))
            print("  Suspected missing sequence numbers:", total_missing)
            print("  Gap ranges (first 10):", gaps[:10] if gaps else "none")
            print("  Reboots suspected (uptime decreased):", restarts)
            if latencies:
                print("  Median time delta (ms):", round(statistics.median(latencies), 1))
                if abs(statistics.median(latencies)) > 5000:
                    print("  [WARNING] Large time delta may be clock skew; NOT proof of MQTT network latency.")

        print("\nNotes:")
        print("- Gaps can mean missing publishes, device errors, or collector downtime; they are not proof of packet loss.")
        print("- A reboot resets sequence; this audit uses uptime to avoid counting cross-reboot gaps.")
        print("- Exact same (device, time, sequence) indicates a suspected duplicate, not guaranteed without boot_id.")
        print("- Retention is based on the time-series time field; avoid timestamps offset from real UTC.")


if __name__ == "__main__":
    try:
        main()
    except (PyMongoError, OSError) as exc:
        print("[ERROR] MongoDB connection/permission issue:", type(exc).__name__)
        raise SystemExit(1)
