"""IoT Lab 2 / Buoc 4: preprocess MongoDB iot_raw -> iot_processed.

Python 3.11+; pip install pymongo python-dotenv certifi pandas numpy.
Keep in the same folder as the working Step 2 .env, or set environment variables.

Usage:
  python preprocess_iot.py --demo
  python preprocess_iot.py --hours 6 --window-seconds 30
  python preprocess_iot.py --hours 6 --window-seconds 30 --dry-run

Raw time-series collection is NEVER changed. Processed output is an ordinary
MongoDB collection with a unique key (device_id, bucket time, window_seconds)
to allow safe, repeatable upserts. MongoDB time-series cannot enforce this
unique index on arbitrary sample keys. The assignment allows a new collection.
"""

import argparse
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import certifi
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne
from pymongo.errors import PyMongoError

load_dotenv()

RAW_NAME = os.getenv("MONGO_COLLECTION", "iot_raw").strip()
DB_NAME = os.getenv("MONGO_DB", "iot_lab2").strip()
PROCESSED_NAME = os.getenv("MONGO_PROCESSED_COLLECTION", "iot_processed").strip()
URI = os.getenv("MONGO_URI", "").strip()
SENSORS = ("temperature", "humidity", "distance_cm")
OTHER_NUMBERS = ("rssi",)
# Physical plausibility filter, not the statistical IQR filter.
LIMITS = {
    "temperature": (-40.0, 80.0),
    "humidity": (0.0, 100.0),
    "distance_cm": (0.1, 450.0),
    "rssi": (-130.0, 0.0),
}


def number_or_none(x: Any):
    """Encode missing numeric values as BSON null rather than NaN."""
    if x is None or pd.isna(x):
        return None
    return float(x)


def to_docs_for_device(raw_docs: list[dict], device: str, window_seconds: int):
    """Pure preprocessing function: can be demo-tested without any cloud login."""
    if not raw_docs:
        return [], {}

    df = pd.DataFrame(raw_docs)
    if "time" not in df.columns:
        return [], {"error": "Missing time field"}
    df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
    bad_times = int(df["time"].isna().sum())
    df = df.dropna(subset=["time"]).copy()
    if df.empty:
        return [], {"error": "No valid dates"}

    for key in ("sequence", "uptime_s"):
        if key not in df:
            df[key] = np.nan
    df["sequence"] = pd.to_numeric(df["sequence"], errors="coerce")
    df["uptime_s"] = pd.to_numeric(df["uptime_s"], errors="coerce")
    df = df.sort_values("time", kind="stable")
    # Exact delivery duplicates only. Reboots can repeat sequence numbers.
    duplicates = int(df.duplicated(subset=["time", "sequence"]).sum())
    df = df.drop_duplicates(subset=["time", "sequence"], keep="first").copy()

    for key in (*SENSORS, *OTHER_NUMBERS):
        if key not in df:
            df[key] = np.nan
        df[key] = pd.to_numeric(df[key], errors="coerce")
        df[key] = df[key].replace([np.inf, -np.inf], np.nan)
        minimum, maximum = LIMITS[key]
        df.loc[~df[key].between(minimum, maximum) & df[key].notna(), key] = np.nan

    invalid_readings = int(df[list(SENSORS)].isna().sum().sum())
    df = df.set_index("time").sort_index()

    clean = df[list(SENSORS)].copy()
    flags = pd.DataFrame(index=clean.index)
    limits_summary = {}
    for key in SENSORS:
        series = clean[key].dropna()
        # Robust IQR on sufficiently variable samples. Constant test settings
        # have IQR=0 and are NOT automatically classified as invalid.
        if len(series) >= 8:
            q1, q3 = series.quantile([0.25, 0.75])
            iqr = q3 - q1
        else:
            iqr = 0.0
        if iqr > 0:
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            flag = clean[key].notna() & ~clean[key].between(lower, upper)
            limits_summary[key] = [round(float(lower), 2), round(float(upper), 2)]
        else:
            flag = pd.Series(False, index=clean.index)
            limits_summary[key] = None
        flags[key] = flag.astype(int)
        clean.loc[flag, key] = np.nan

    # Average readings per bucket, including buckets with zero observations.
    freq = f"{window_seconds}s"
    average = clean.resample(freq).mean()
    samples = df["sequence"].resample(freq).size().reindex(average.index, fill_value=0)
    anomaly_count = flags.resample(freq).sum().sum(axis=1).reindex(average.index, fill_value=0)
    missing_window = samples.eq(0)
    missing_buckets = int(missing_window.sum())

    # Fill at most TWO internal empty buckets (~60 sec at 30 sec/window).
    # No forward/back fill at series ends, or interpolation across long outages.
    for key in SENSORS:
        average[key] = average[key].interpolate(
            method="time", limit=2, limit_area="inside"
        )
        average[f"{key}_rolling_mean_3"] = average[key].rolling(
            window=3, min_periods=1
        ).mean()
        average[f"{key}_delta"] = average[key].diff()
        valid = average[key].dropna()
        if len(valid) and float(valid.max()) > float(valid.min()):
            average[f"{key}_norm"] = (
                (average[key] - valid.min()) / (valid.max() - valid.min())
            )
        else:
            # Constant or missing input: normalized output is 0 / missing.
            average[f"{key}_norm"] = average[key].where(average[key].isna(), 0.0)

    now = datetime.now(timezone.utc)
    out = []
    for t, row in average.iterrows():
        doc = {
            "time": t.to_pydatetime(),
            "metadata": {"device_id": device},
            "window_seconds": window_seconds,
            "sample_count": int(samples.loc[t]),
            "was_missing_window": bool(missing_window.loc[t]),
            "outlier_count": int(anomaly_count.loc[t]),
            "processed_at": now,
            "source_collection": RAW_NAME,
        }
        for key in SENSORS:
            for field in (key, f"{key}_rolling_mean_3", f"{key}_delta", f"{key}_norm"):
                doc[field] = number_or_none(row[field])
        out.append(doc)
    stats = {
        "raw_count": len(raw_docs),
        "valid_date_count": len(df),
        "bad_times": bad_times,
        "duplicates_dropped": duplicates,
        "invalid_sensor_values": invalid_readings,
        "outliers_detected": int(flags.sum().sum()),
        "outliers_by_sensor": {key: int(flags[key].sum()) for key in SENSORS},
        "iqr_bounds": limits_summary,
        "missing_windows": missing_buckets,
        "total_buckets": len(out),
        "has_value_buckets": sum(row["temperature"] is not None for row in out),
    }
    return out, stats


def test_samples():
    """Small offline demo, includes gap, duplicate, invalid value and spike."""
    start = datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc)
    demo = []
    for i in range(100):
        if 30 <= i < 44:  # 70s gap -> empty 30s windows
            continue
        temp = 25.0 if i < 50 else 31.0
        if i == 76:
            temp = 79.0  # physically possible but statistically unusual
        demo.append({
            "time": start + timedelta(seconds=i * 5),
            "sequence": i + 1,
            "temperature": temp,
            "humidity": None if i == 12 else (50.0 if i < 50 else 67.0),
            "distance_cm": 999.0 if i == 17 else (100.0 if i < 50 else 35.0),
        })
    demo.append(demo[20].copy())
    return demo


def get_args():
    parser = argparse.ArgumentParser(description="IoT data preprocessing MongoDB Atlas")
    parser.add_argument("--hours", type=float, default=6.0,
                        help="Process newest N hours of sensor history (default: 6)")
    parser.add_argument("--window-seconds", type=int, default=30,
                        choices=[15, 30, 60, 300], help="Resample duration in seconds")
    parser.add_argument("--max-records", type=int, default=20000,
                        help="Safety limit; abort if exceeded")
    parser.add_argument("--device", type=str, default=None,
                        help="Optional device ID filter")
    parser.add_argument("--dry-run", action="store_true",
                        help="Calculate and print summaries, do not save processed data")
    parser.add_argument("--demo", action="store_true",
                        help="Test algorithm with synthetic data, without Atlas connection")
    return parser.parse_args()


def print_report(device, stats, docs, saved=False):
    print(f"\n=== DEVICE: {device} ===")
    print("Raw read:", stats.get("raw_count", 0))
    print("Duplicate time+sequence removed:", stats.get("duplicates_dropped", 0))
    print("Missing/invalid sensor values:", stats.get("invalid_sensor_values", 0))
    print("Statistical outliers IQR:", stats.get("outliers_detected", 0),
          stats.get("outliers_by_sensor"))
    print("Empty time buckets:", stats.get("missing_windows", 0))
    print("Resampled buckets:", stats.get("total_buckets", 0))
    if docs:
        print("First bucket:", docs[0]["time"].isoformat())
        print("Last bucket:", docs[-1]["time"].isoformat())
        d = next((x for x in docs if x["temperature"] is not None), docs[0])
        print("Example:", {k: d.get(k) for k in
                          ("temperature", "temperature_rolling_mean_3",
                           "temperature_delta", "temperature_norm", "sample_count")})
    if saved:
        print("[OK] Upserted processed data")


def main():
    a = get_args()
    if a.demo:
        docs, stats = to_docs_for_device(test_samples(), "esp32_demo", a.window_seconds)
        print_report("esp32_demo", stats, docs)
        print("[DEMO OK] No Atlas write attempted")
        return

    if a.hours <= 0 or a.max_records <= 0:
        raise SystemExit("[ERROR] --hours and --max-records must be > 0")
    if not URI or "<" in URI or ">" in URI:
        raise SystemExit("[ERROR] Missing/placeholder MONGO_URI in .env")

    with MongoClient(URI, tz_aware=True, tzinfo=timezone.utc,
                     tlsCAFile=certifi.where(), serverSelectionTimeoutMS=12000) as client:
        client.admin.command("ping")
        db = client[DB_NAME]
        raw = db[RAW_NAME]
        conditions = {}
        if a.device:
            conditions["metadata.device_id"] = a.device
        newest = raw.find_one(conditions, {"time": 1}, sort=[("time", DESCENDING)])
        if not newest:
            raise SystemExit("[ERROR] No raw data. Check iot_raw or restart Wokwi + MQTT collector.")
        latest_time = newest["time"]
        start_time = latest_time - timedelta(hours=a.hours)
        query = {**conditions, "time": {"$gte": start_time, "$lte": latest_time}}
        projection = {"_id": 0, "time": 1, "metadata.device_id": 1, "sequence": 1,
                      "uptime_s": 1, "temperature": 1, "humidity": 1, "distance_cm": 1,
                      "rssi": 1}
        raw_docs = list(raw.find(query, projection).sort("time", ASCENDING).limit(a.max_records + 1))
        if len(raw_docs) > a.max_records:
            raise SystemExit("[ERROR] Too many records. Use shorter --hours or raise --max-records.")

        print("[MONGO] Connected to Atlas")
        print(f"[RAW] {DB_NAME}.{RAW_NAME} | n={len(raw_docs)}")
        print("[TIME]", start_time.isoformat(), "->", latest_time.isoformat())
        print("[WINDOW]", a.window_seconds, "seconds")
        if len(raw_docs) < 5:
            raise SystemExit("[ERROR] Too few samples. Collect at least 5 readings first.")
        groups = {}
        for d in raw_docs:
            device = d.get("metadata", {}).get("device_id")
            if isinstance(device, str) and device.strip():
                groups.setdefault(device, []).append(d)

        processed = None
        if not a.dry_run:
            found = next(db.list_collections(filter={"name": PROCESSED_NAME}), None)
            if found and "timeseries" in found.get("options", {}):
                raise SystemExit(
                    "[ERROR] iot_processed already exists as Time Series. "
                    "Do not delete it. Set MONGO_PROCESSED_COLLECTION=iot_processed_v2 in .env."
                )
            processed = db[PROCESSED_NAME]
            processed.create_index(
                [("metadata.device_id", ASCENDING),
                 ("time", ASCENDING), ("window_seconds", ASCENDING)],
                unique=True, name="uniq_device_time_window"
            )
            # Seven-day TTL for processed records (the source also has its own TTL).
            processed.create_index(
                [("time", ASCENDING)], expireAfterSeconds=604800,
                name="ttl_processed_time_7d"
            )
        total_upserts = 0
        for device, source in sorted(groups.items()):
            docs, stats = to_docs_for_device(source, device, a.window_seconds)
            if not docs:
                print(f"[SKIP] {device}: could not preprocess")
                continue
            if processed is not None:
                ops = [ReplaceOne(
                    {"metadata.device_id": device, "time": d["time"],
                     "window_seconds": a.window_seconds},
                    d, upsert=True
                ) for d in docs]
                result = processed.bulk_write(ops, ordered=False)
                total_upserts += result.upserted_count + result.modified_count
            print_report(device, stats, docs, saved=processed is not None)
        if a.dry_run:
            print("\n[DRY RUN] Data not written to MongoDB")
        else:
            print(f"\n[DONE] Processed data saved in {DB_NAME}.{PROCESSED_NAME}")
            print("Run Data Explorer ->", DB_NAME, "->", PROCESSED_NAME, "-> Find")
            print("Save count (upserted + modified):", total_upserts)
            print("[NOTE] Re-running replaces existing processed windows (no duplicate rows).")
            print("[NOTE] IQR flags may represent deliberate Wokwi parameter changes.")


if __name__ == "__main__":
    try:
        main()
    except (PyMongoError, OSError) as e:
        print("[ERROR] MongoDB connection/write issue:", type(e).__name__, str(e)[:200])
        raise SystemExit(1)
