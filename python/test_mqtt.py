import json
import paho.mqtt.client as mqtt

BROKER = "broker.hivemq.com"
PORT = 1883
TOPIC = "iot/lab2/lab2_7f26b9/esp32_01/telemetry"


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("MQTT connected. Subscribing:", TOPIC)
        client.subscribe(TOPIC)
    else:
        print("MQTT connection failed:", reason_code)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        print("RECEIVED", msg.topic, json.dumps(data, ensure_ascii=False))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print("Invalid payload:", exc)


client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, PORT, keepalive=60)
print("Waiting for ESP32 telemetry... (Ctrl+C to stop)")
try:
    client.loop_forever()
except KeyboardInterrupt:
    client.disconnect()
