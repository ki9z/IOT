"""IoT lab 2: ESP32/Wokwi -> MQTT -> Python -> MongoDB Atlas Time Series."""
import json
import math
import os
from datetime import datetime, timezone

import certifi
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "").strip()
DB_NAME = os.getenv("MONGO_DB", "iot_lab2")
COLL_NAME = os.getenv("MONGO_COLLECTION", "iot_raw")
BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = os.getenv("MQTT_TOPIC", "iot/lab2/lab2_7f26b9/esp32_01/telemetry")

if not MONGO_URI or "<" in MONGO_URI or ">" in MONGO_URI:
    raise SystemExit("Chua dien MONGO_URI hop le trong file .env")

mongo_client = MongoClient(
    MONGO_URI,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=12000,
)

def ensure_collection():
    db = mongo_client[DB_NAME]
    found = next(db.list_collections(filter={"name": COLL_NAME}), None)
    if found is None:
        db.create_collection(
            COLL_NAME,
            timeseries={"timeField": "time", "metaField": "metadata", "granularity": "seconds"},
            expireAfterSeconds=7 * 24 * 60 * 60,
        )
        print(f"Created time-series collection: {DB_NAME}.{COLL_NAME}")
    elif "timeseries" not in found.get("options", {}):
        raise RuntimeError(
            f"{DB_NAME}.{COLL_NAME} da ton tai NHUNG khong phai Time Series. "
            "Hay tao collection Time Series hoac doi MONGO_COLLECTION trong .env."
        )
    else:
        print(f"Using time-series collection: {DB_NAME}.{COLL_NAME}")
    return db[COLL_NAME]


def require_float(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: khong phai so")
    v = float(value)
    if not math.isfinite(v) or not minimum <= v <= maximum:
        raise ValueError(f"{name}: ngoai pham vi")
    return v


def require_integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}: phai la so nguyen")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}: ngoai pham vi")
    return value


def validate_payload(payload: bytes) -> dict:
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON phai la object")

    device_id = data.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 64:
        raise ValueError("device_id khong hop le")

    now = datetime.now(timezone.utc)
    timestamp_ms = require_integer(data.get("timestamp"), "timestamp", 1_577_836_800_000, 4_102_444_800_000)
    sensor_time = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
    if (sensor_time - now).total_seconds() > 86400:
        raise ValueError("timestamp nam qua xa trong tuong lai")

    temperature = require_float(data.get("temperature"), "temperature", -40, 80)
    humidity = require_float(data.get("humidity"), "humidity", 0, 100)
    distance = require_float(data.get("distance_cm"), "distance_cm", 0.1, 450)
    rssi = require_integer(data.get("rssi"), "rssi", -130, 0)
    seq = require_integer(data.get("sequence"), "sequence", 0, 2**32 - 1)
    uptime = require_integer(data.get("uptime_s"), "uptime_s", 0, 2**32 - 1)

    return {
        "time": sensor_time,  # BSON Date: timeField MongoDB
        "metadata": {"device_id": device_id},  # metaField MongoDB
        "temperature": temperature,
        "humidity": humidity,
        "distance_cm": distance,
        "rssi": rssi,
        "sequence": seq,
        "uptime_s": uptime,
        "received_at": now,
        "latency_ms": round((now - sensor_time).total_seconds() * 1000, 1),
    }


collection = None
seen_sequences = {}  # Dedup don gian trong 1 lan chay


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("[MQTT] Connected. Subscribe:", TOPIC)
        client.subscribe(TOPIC, qos=0)
    else:
        print("[MQTT] Connection failed:", reason_code)


def on_message(client, userdata, message):
    try:
        doc = validate_payload(message.payload)
        device = doc["metadata"]["device_id"]
        seq = doc["sequence"]
        key = (seq, doc["time"])
        if seen_sequences.get(device) == key:
            print(f"[SKIP] Duplicate in session: {device} #{seq}")
            return
        doc["source_topic"] = message.topic
        result = collection.insert_one(doc)
        seen_sequences[device] = key
        print(
            f"[SAVED] seq={seq} T={doc['temperature']:.1f}C "
            f"H={doc['humidity']:.1f}% dist={doc['distance_cm']:.1f}cm "
            f"latency={doc['latency_ms']}ms id={result.inserted_id}"
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, OverflowError) as exc:
        print("[INVALID]", exc)
    except PyMongoError as exc:
        print("[DB ERROR]", type(exc).__name__, "- kiem tra Atlas, IP/credentials/ket noi")


def main():
    global collection
    mongo_client.admin.command("ping")
    print("[MONGO] Connected to Atlas")
    collection = ensure_collection()
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(BROKER, PORT, keepalive=60)
    print("[READY] Start simulation Wokwi. Ctrl+C to stop.")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.disconnect()
        mongo_client.close()


if __name__ == "__main__":
    try:
        main()
    except (PyMongoError, RuntimeError, OSError) as exc:
        print("[ERROR]", type(exc).__name__, "- check .env, credentials, cluster network, and correct time-series collection")
        raise SystemExit(1)
