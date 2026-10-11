#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <ArduinoJson.h>
#include <time.h>
#include <sys/time.h>
#include <math.h>

// 1. WiFi mo phong Wokwi
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";

// 2. MQTT public broker (chi su dung du lieu demo)
const char* MQTT_SERVER = "broker.hivemq.com";
const int MQTT_PORT = 1883;
// Doi ma "lab2_7f26b9" thanh chuoi rieng cua ban neu can.
const char* MQTT_TOPIC = "iot/lab2/lab2_7f26b9/esp32_01/telemetry";
const char* DEVICE_ID = "esp32_01";

// 3. Cam bien va LED: giu nguyen mach Bai 1
const int DHT_PIN = 15;
const int TRIG_PIN = 5;
const int ECHO_PIN = 18;
const int LED_PIN = 2;

const unsigned long SEND_INTERVAL_MS = 5000;

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
DHTesp dht;

unsigned long lastSend = 0;
unsigned long lastMqttAttempt = 0;
unsigned long lastWifiAttempt = 0;
uint32_t sequenceNo = 0;

// 4. Ket noi WiFi: co timeout de khong treo mai
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("Connecting WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  for (int i = 0; i < 30 && WiFi.status() != WL_CONNECTED; i++) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi connected, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi not connected. Will retry.");
  }
}

// 5. Ket noi MQTT va tu dong thu lai khi mat ket noi
void connectMQTT() {
  if (WiFi.status() != WL_CONNECTED || mqttClient.connected()) return;
  unsigned long now = millis();
  if (lastMqttAttempt != 0 && now - lastMqttAttempt < 3000) return;
  lastMqttAttempt = now;

  String clientId = "esp32-lab2-";
  clientId += String((uint32_t)ESP.getEfuseMac(), HEX);
  clientId += "-";
  clientId += String((uint32_t)random(0xFFFF), HEX);

  Serial.print("Connecting MQTT...");
  if (mqttClient.connect(clientId.c_str())) {
    Serial.println(" connected!");
    Serial.print("Publishing topic: ");
    Serial.println(MQTT_TOPIC);
  } else {
    Serial.print(" failed, rc=");
    Serial.println(mqttClient.state());
  }
}

// 6. Do khoang cach theo cm
float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) return NAN;
  return duration * 0.0343f / 2.0f;
}

// 7. Tao JSON va publish moi 5 giay
void publishSensorData() {
  TempAndHumidity data = dht.getTempAndHumidity();
  float distance = readDistanceCm();

  if (!isfinite(data.temperature) || !isfinite(data.humidity) ||
      !isfinite(distance) || data.humidity < 0 || data.humidity > 100 ||
      distance <= 0) {
    Serial.println("Invalid sensor data - skip publish");
    return;
  }

  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) {
    Serial.println("NTP time not ready - skip publish");
    return;
  }
  uint64_t timestampMs = (uint64_t)tv.tv_sec * 1000ULL +
                         (uint64_t)tv.tv_usec / 1000ULL;

  sequenceNo++;
  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["timestamp"] = timestampMs;
  doc["temperature"] = roundf(data.temperature * 100) / 100;
  doc["humidity"] = roundf(data.humidity * 100) / 100;
  doc["distance_cm"] = roundf(distance * 100) / 100;
  doc["rssi"] = WiFi.RSSI();
  doc["sequence"] = sequenceNo;
  doc["uptime_s"] = millis() / 1000;

  char payload[384];
  if (measureJson(doc) >= sizeof(payload)) {
    Serial.println("JSON too long - skip publish");
    return;
  }
  size_t length = serializeJson(doc, payload, sizeof(payload));
  bool ok = mqttClient.publish(MQTT_TOPIC,
                               (const uint8_t*)payload,
                               (unsigned int)length,
                               false);
  Serial.print(payload);
  Serial.println(ok ? " | publish=OK" : " | publish=FAILED");
  digitalWrite(LED_PIN, HIGH);
  delay(80);
  digitalWrite(LED_PIN, LOW);
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  dht.setup(DHT_PIN, DHTesp::DHT22);
  randomSeed(micros());

  connectWiFi();
  configTime(0, 0, "pool.ntp.org", "time.google.com"); // UTC
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(512);
  mqttClient.setKeepAlive(30);
  Serial.println("IoT LAB 2 - STEP 1 STARTED");
}

void loop() {
  unsigned long now = millis();
  if (WiFi.status() != WL_CONNECTED &&
      (lastWifiAttempt == 0 || now - lastWifiAttempt >= 5000)) {
    lastWifiAttempt = now;
    connectWiFi();
  }
  if (!mqttClient.connected()) connectMQTT();
  mqttClient.loop();

  now = millis();
  if (mqttClient.connected() && now - lastSend >= SEND_INTERVAL_MS) {
    lastSend = now;
    publishSensorData();
  }
  delay(10);
}
