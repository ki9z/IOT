# IoT Lab 2 – Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

**Học phần:** IoT và Ứng dụng (INT14149) · **Nền tảng:** ESP32 trên Wokwi + HiveMQ Public MQTT + Python + MongoDB Atlas + Streamlit.

> Bài thực hành theo PDF: thu thập và subscribe MQTT, kiểm tra dữ liệu, lưu Time-Series, quản lý trùng/mất, tiền xử lý (missing/outlier/resampling/rolling mean/delta/normalization), trực quan hóa và đánh giá hiệu năng.

## 1. Cấu trúc mã nguồn

```text
IOT/
├── firmware/
│   ├── sketch.ino
│   ├── diagram.json
│   └── libraries.txt
├── python/
│   ├── .env.example
│   ├── requirements.txt
│   ├── mqtt_to_mongodb.py
│   ├── mqtt_to_mongodb_v2.py
│   ├── latency_utils.py
│   ├── test_latency_utils.py
│   ├── test_mongodb.py
│   ├── test_mqtt.py
│   ├── step3_check.py
│   ├── preprocess_iot.py
│   ├── evaluate_iot.py
│   ├── dashboard_iot.py
│   ├── dashboard_metrics.py
│   └── test_dashboard_metrics.py
├── .github/workflows/python-checks.yml
├── .gitignore
└── README.md
```

## 2. Kiến trúc

```text
ESP32 + DHT22 + HC-SR04 (Wokwi)
       |
       | JSON publish mỗi 5 giây; QoS 0
       v
broker.hivemq.com:1883 (Public MQTT)
       |
       | Python paho-mqtt subscribe, validate, deduplicate best-effort
       v
MongoDB Atlas / iot_lab2.iot_raw (Time-Series, giữ tối đa khoảng 7 ngày)
       |
       +--> preprocess_iot.py --> iot_processed (IQR, 30s, rolling/delta/Min-Max)
       |
       +--> mqtt_to_mongodb_v2.py --> iot_write_metrics (thời gian ghi đo bằng perf_counter_ns)
       |
       v
Streamlit dashboard_iot.py (live/raw/processed/quality/write timing)
```

**Lưu ý:** Broker công cộng chỉ dành cho dữ liệu demo; không gửi mật khẩu hay dữ liệu cá nhân.

## 3. Tạo MongoDB Atlas

- Database: `iot_lab2`.
- Collection **`iot_raw`**: loại **Time-Series**; `timeField=time`; `metaField=metadata`; `granularity=seconds`; `expireAfterSeconds=604800` (7 ngày).
- Collection `iot_processed`: chương trình tiền xử lý tự tạo collection thường có unique index và TTL 7 ngày.
- Collection `iot_write_metrics`: collector V2 tự tạo khi nhận bản tin mới; lưu thời điểm MongoDB xác nhận và phép đo hiệu năng ghi; cố gắng tạo TTL 7 ngày.
- Trong Atlas **Database Access**, tạo user có quyền ghi; **Network Access**, cho phép IP máy đang dùng.

## 4. Chạy Wokwi

1. Tạo/copy dự án ESP32 trên [Wokwi](https://wokwi.com/).
2. Dán nội dung ba file trong `firmware/` vào `sketch.ino`, `diagram.json`, `libraries.txt`.
3. Bật mô phỏng. Serial Monitor cần xuất hiện `Connecting MQTT... connected!` và `publish=OK`.
4. MQTT topic trong sketch phải **giống chính xác** `MQTT_TOPIC` trong `.env`.

## 5. Cài Python và chạy từng chương trình (Windows PowerShell)

Cần Python 3.11 hoặc mới hơn, kết nối Internet. Mở PowerShell tại `IOT/python`.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Điền `MONGO_URI` từ MongoDB Atlas (Database → Connect → Drivers → Python), mật khẩu database user và `MQTT_TOPIC` trùng firmware. Không đưa `.env` lên GitHub.

**Test kết nối Atlas:**

```powershell
python test_mongodb.py
```

**Thu thập và ghi dữ liệu liên tục (chỉ chạy 1 collector):**

```powershell
python mqtt_to_mongodb_v2.py
```

Bạn sẽ thấy `[SAVED] ... db_write=...ms, collector_to_db=...ms`; log `[DUPLICATE SKIPPED]` là bản tin đã tồn tại. Nhấn Ctrl+C để dừng.

**Kiểm tra Time-Series và số sequence thiếu:**

```powershell
python step3_check.py
```

**Tiền xử lý:**

```powershell
python preprocess_iot.py --demo
python preprocess_iot.py --hours 6 --window-seconds 30 --dry-run
python preprocess_iot.py --hours 6 --window-seconds 30
```

**Dashboard:** Mở PowerShell khác tại cùng thư mục:

```powershell
python -m streamlit run dashboard_iot.py
```

Mở [http://localhost:8501](http://localhost:8501), chọn thiết bị `esp32_01` và tab **Chất lượng và độ trễ** để xem phép đo ghi MongoDB. Nếu chưa thấy `iot_write_metrics`, hãy chạy collector V2 **bản mới** và để nhận thêm khoảng 10–20 bản tin.

**Xuất số liệu báo cáo:**

```powershell
python evaluate_iot.py
notepad metrics_report.txt
```

## 6. Phương pháp đo độ trễ – cần phân biệt đúng

- `mongodb_insert_ms`: `perf_counter_ns()` ngay trước → ngay sau khi `insert_one()` được xác nhận. Là thời gian ghi từ phía Python (bao gồm round-trip DB).
- `collector_to_db_ms`: `perf_counter_ns()` ngay khi callback MQTT bắt đầu → ngay sau `insert_one()`, bao gồm kiểm tra, truy vấn trùng/gap và insert.
- `observed_sensor_to_store_ms`: `stored_at - time`. **Chỉ là chênh lệch timestamp hai đồng hồ**, chưa thể xem là end-to-end chính xác khi chưa xác minh đồng bộ NTP giữa Wokwi và máy Python.
- Dữ liệu lịch sử trước khi thêm `iot_write_metrics` **không thể đo lại thời gian insert chính xác**, cần thu thập thêm dữ liệu mới.

Để đánh giá end-to-end theo nghĩa thiết bị → DB, cần đo/kiểm chứng sai lệch đồng hồ trên cả hai phía và báo rõ sai số. Đừng gọi `received_at - time` là độ trễ MQTT thuần.

## 7. Kết quả thực nghiệm đã xác nhận

Báo cáo lấy tại **11/10/2026 02:31:41 UTC**:

| Chỉ tiêu | Kết quả |
|---|---:|
| Số bản ghi gốc `iot_raw` | 493 |
| Cửa sổ xử lý `iot_processed` | 47 |
| Bản ghi nghi trùng (sequence,time) | 93 |
| Sequence nghi thiếu | 3 |
| Số lần nghi reboot | 0 |
| Trung vị chu kỳ ESP32 | 5006 ms |
| Trung vị chu kỳ nhận Python | 5007 ms |
| `received_at - time` trung vị | 253040,5 ms (chứa sai lệch đồng hồ) |
| Retention `iot_raw` | 604800 giây |

*Các giá trị đo ghi MongoDB mới được bổ sung ở collector V2 phiên bản này; chạy `evaluate_iot.py` sau khi thu thập thêm để ghi nhận chúng.*

## 8. Kiểm thử source không cần tài khoản Atlas

```powershell
python -m compileall -q .
python -m unittest -v test_latency_utils.py
python test_dashboard_metrics.py
python preprocess_iot.py --demo
```

Workflow GitHub Actions tự chạy các kiểm thử này khi push lên `main`. Chạy thử từ bản clone mới để kiểm tra `README`, không chỉ từ môi trường cũ.

## 9. Giới hạn và bảo mật

- QoS 0 có thể làm thiếu bản tin khi mạng ngắt; `GAP WARNING` chỉ là dấu hiệu chứ không chứng minh đã mất gói do mạng.
- Chống trùng trong V2 là kiểm tra bằng truy vấn trước insert, **không bảo đảm giao dịch nguyên tử với nhiều collector cùng lúc**.
- `iot_processed` chỉ thay đổi khi chạy lại `preprocess_iot.py`, còn dashboard tự làm mới dữ liệu đã có trong Atlas.
- **Không commit** `.env`, Access Token ThingsBoard, mật khẩu MongoDB, khóa riêng hoặc ảnh có thông tin đăng nhập. Repo nên được kiểm tra bảo mật trước khi công khai.
- Máy phòng thực hành có thể đóng băng ổ C: lưu source, ảnh minh chứng lên GitHub/USB/Drive.

## 10. Bộ hồ sơ cần nộp

Theo PDF: source trên GitHub, báo cáo Word 4–6 trang và ứng dụng demo. Chụp ảnh mạch Wokwi, publish MQTT, collector lưu MongoDB, schema + TTL, bản ghi xử lý, Dashboard và thống kê hiệu năng. Báo cáo bổ sung sau khi có ảnh thật và số liệu mới.
