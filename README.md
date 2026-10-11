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
│   ├── dashboard_data.py
│   ├── test_dashboard_app.py
│   ├── test_dashboard_data.py
│   ├── test_preprocessing.py
│   ├── dashboard_metrics.py
│   └── test_dashboard_metrics.py
├── docs/REPORT_GUIDE.md
├── .streamlit/config.toml
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

## 7. Số liệu lịch sử (cần đo lại sau nâng cấp)

Các số liệu dưới đây được ghi trong README trước nâng cấp, tại **11/10/2026 02:31:41 UTC**. Chưa kiểm chứng lại trong nhánh này; không sử dụng như kết quả mới:

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
python -m unittest -v test_latency_utils test_preprocessing test_dashboard_data test_dashboard_app
python test_dashboard_metrics.py
python preprocess_iot.py --demo
```

Workflow GitHub Actions tự chạy các kiểm thử này cho Pull Request và khi push lên `main`. AppTest kiểm tra thao tác giao diện với dữ liệu giả lập/mocked MongoDB, không gọi Atlas. Chạy thử từ bản clone mới để kiểm tra `README`, không chỉ từ môi trường cũ.

## 9. Giới hạn và bảo mật

- QoS 0 có thể làm thiếu bản tin khi mạng ngắt; `GAP WARNING` chỉ là dấu hiệu chứ không chứng minh đã mất gói do mạng.
- Chống trùng trong V2 là kiểm tra bằng truy vấn trước insert, **không bảo đảm giao dịch nguyên tử với nhiều collector cùng lúc**.
- `iot_processed` chỉ thay đổi khi chạy CLI hoặc bấm nút lưu trong app; dashboard tự làm mới không tự chạy pipeline. Nút lưu mặc định bị khóa, bật bằng `DASHBOARD_ALLOW_WRITES=true`.
- **Không commit** `.env`, Access Token ThingsBoard, mật khẩu MongoDB, khóa riêng hoặc ảnh có thông tin đăng nhập. Repo nên được kiểm tra bảo mật trước khi công khai.
- Máy phòng thực hành có thể đóng băng ổ C: lưu source, ảnh minh chứng lên GitHub/USB/Drive.

## 10. Bộ hồ sơ cần nộp

Theo PDF: source trên GitHub, báo cáo Word 4–6 trang và ứng dụng demo. Chụp ảnh mạch Wokwi, publish MQTT, collector lưu MongoDB, schema + TTL, bản ghi xử lý, Dashboard và thống kê hiệu năng. Báo cáo bổ sung sau khi có ảnh thật và số liệu mới.


## 11. Ứng dụng Sensor Studio sau nâng cấp

**Giao diện:** nền sáng, thẻ KPI, biểu đồ trong card, màu cảm biến nhất quán, bố cục linh hoạt. Bốn tab:

| Tab | Chức năng |
|---|---|
| Giám sát cảm biến | Giá trị mới nhất, delta, biểu đồ 4 kênh; độ mới dựa trên `received_at`; MongoDB báo theo kết quả đọc, MQTT không giả định đang online |
| Tiền xử lý | Chọn cửa sổ, xem trước pipeline và thống kê từng cảm biến; tùy chọn lưu thực vào collection mới; so sánh raw/processed, rolling, delta, Min–Max |
| Chất lượng & độ trễ | Trùng/gap/reboot; thiếu và ngoài miền; mean/P95/min/max thời gian ghi; biểu đồ/histogram; xuất JSON đánh giá |
| Bảng dữ liệu | Chọn raw/processed, chọn cột, thứ tự, phân trang, xuất toàn bộ khoảng đã tải ra CSV |

**Chạy từ thư mục gốc repo** (để nạp `.streamlit/config.toml`):

```powershell
python -m pip install -r python/requirements.txt
# Nếu chưa có .env: Copy-Item python/.env.example python/.env
python -m streamlit run python/dashboard_iot.py
```

Cấu hình mặc định chỉ lắng nghe `127.0.0.1`, dùng URL `localhost`, tắt usage telemetry và không dò IP bên ngoài khi khởi động.

App và preprocessing luôn đọc `.env` nằm trong `python/` ngay cả khi chạy từ repo root. Collector và các script kiểm tra còn lại nên chạy từ `python/` như phần 5.

**Thử giao diện hoàn toàn offline:** Bật checkbox “Dùng dữ liệu demo offline” trên sidebar, hoặc:

```powershell
$env:IOT_DEMO="true"
python -m streamlit run python/dashboard_iot.py
# Sau khi dừng bằng Ctrl+C, bỏ biến để dùng lại Atlas:
Remove-Item Env:IOT_DEMO
```

Demo có dữ liệu thiếu, spike và trùng để minh họa pipeline. Các cảm biến và phép đo thời gian đều là giả lập, luôn có nhãn DEMO; không kết nối hoặc ghi MongoDB.

**Lưu tiền xử lý từ app:** đặt `DASHBOARD_ALLOW_WRITES=true` trong `python/.env`, khởi động lại Streamlit, tắt demo, chọn thiết bị/thời gian/cửa sổ. Bấm **Xem trước tiền xử lý**, đọc kết quả rồi **Chạy và lưu kết quả**. Cần tài khoản DB có quyền tạo chỉ mục và ghi collection xử lý. App không xóa/chỉnh dữ liệu gốc; không tự chạy xử lý trong chu kỳ refresh. Kết quả xem trước gắn với mốc thời gian của lần bấm, không tự cập nhật khi bản tin mới tới. Không chạy CLI tiền xử lý đồng thời với nút lưu.

**Phạm vi thời gian và giới hạn:** mặc định 30 phút từ giờ máy hiện tại. Muốn xem dữ liệu cũ khi Wokwi đã dừng, bật “Xem lịch sử từ mẫu mới nhất”. Raw và phép đo dùng cùng khoảng thời gian theo `sensor_time`; processed lấy các cửa sổ giao với khoảng đó. Tối đa 12.000 dòng mỗi nguồn; nếu raw bị cắt bớt, app khóa tiền xử lý để tránh lưu từ dữ liệu không đầy đủ. Thu hẹp khoảng chọn nếu cần. Pipeline chặn hơn 10.000 cửa sổ để tránh cấp phát bộ nhớ quá lớn.

**Tương thích:** Python 3.11+, Streamlit 1.50+; cài lại requirements khi cập nhật source. Dữ liệu xử lý cũ vẫn đọc được, chạy lại pipeline để có trường chất lượng v2. Ba collection raw/processed/metrics phải có tên khác nhau; collection processed phải là collection thường. Không tự xóa dữ liệu nếu cấu hình sai.

**Thay đổi thuật toán:** nội suy chỉ điền toàn bộ khoảng trống nội bộ dài ≤2 cửa sổ; không điền một phần khoảng ngừng thu thập dài. Các giá trị thiếu vẫn null; rolling tại cửa sổ còn thiếu và delta qua khoảng thiếu vẫn null. Min–Max tính theo từng lần chạy, nên khi phạm vi chạy thay đổi kết quả cũ có thể được cập nhật. Khóa upsert vẫn `(device_id, time, window_seconds)`; các cửa sổ ngoài phạm vi không bị xóa. `processed_at` cho biết lần xử lý của từng cửa sổ.

## 12. Chuẩn bị báo cáo

Xem [hướng dẫn báo cáo, schema, kịch bản thử và checklist](docs/REPORT_GUIDE.md). Tải CSV và JSON từ app với **dữ liệu thật**, ghi thiết bị và khoảng thời gian, chụp dashboard và pipeline. Các phép đo callback→DB chưa hoàn thành yêu cầu đo end-to-end đã kiểm chứng; tài liệu nêu cách phân biệt và phần cần bổ sung.
