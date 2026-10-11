# IoT Lab 2 — Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

**Học phần:** IoT và Ứng dụng (INT14149).

Hệ thống mô phỏng ESP32 + DHT22 + HC-SR04 bằng Wokwi, gửi JSON 5 giây/lần qua MQTT (HiveMQ public), Python tiếp nhận và lưu dữ liệu vào MongoDB Atlas Time-Series, tiền xử lý bằng Pandas và trực quan hóa với Streamlit.

## Kiến trúc

```text
ESP32 Wokwi --> MQTT broker.hivemq.com --> Python subscriber
                                             |
                                             v
                                  MongoDB Atlas: iot_lab2.iot_raw
                                             |
                                  pandas preprocess_iot.py
                                             |
                                             v
                                  iot_lab2.iot_processed
                                             |
                                             v
                                  Streamlit dashboard_iot.py
```

## Hướng dẫn chạy (Windows)

1. Trên MongoDB Atlas, tạo `iot_lab2.iot_raw` dưới dạng Time-Series (`timeField=time`, `metaField=metadata`, `granularity=seconds`), retention 7 ngày (`604800` giây).
2. Mở Wokwi, chạy các file trong `firmware/`; phải thấy `publish=OK`.
3. Mở PowerShell tại `python/` rồi chạy:
   ```powershell
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   notepad .env
   python test_mongodb.py
   python mqtt_to_mongodb_v2.py
   ```
   Điền chuỗi kết nối Atlas của bạn trong `.env`, đồng bộ `MQTT_TOPIC` với firmware.
4. Ở PowerShell khác:
   ```powershell
   python step3_check.py
   python preprocess_iot.py --hours 6 --window-seconds 30
   python -m streamlit run dashboard_iot.py
   ```
   Mở `http://localhost:8501` để xem Dashboard.
5. Xuất số liệu nghiên cứu: `python evaluate_iot.py`.

## Kết quả kiểm tra (11/10/2026, 02:31:41 UTC)

- 493 bản ghi gốc trong `iot_raw`; 47 cửa sổ đã xử lý.
- Time-Series xác nhận, `timeField=time`, `metaField=metadata`, TTL 604800 giây.
- Trung vị chu kỳ cảm biến 5006 ms; chu kỳ phía Python 5007 ms.
- 93 cặp (sequence, time) nghi trùng, 3 sequence nghi thiếu; chỉ mang tính cảnh báo.
- Chênh lệch `received_at - time` trung vị 253040,5 ms **chưa phải độ trễ mạng/end-to-end**, vì có sai lệch đồng hồ.

## Bảo mật và giới hạn

**Không commit file `.env`, mật khẩu MongoDB, Access Token ThingsBoard, khóa API hoặc ảnh có thông tin đăng nhập.** Chỉ commit `.env.example` không có bí mật. HiveMQ public không có xác thực, chỉ sử dụng cho bài thực hành.

Không chạy hai MQTT collector cùng lúc. MongoDB Time-Series không hỗ trợ unique index cho (device, time, sequence); chương trình V2 bỏ qua tin nghi trùng trong phạm vi xử lý của ứng dụng. Dữ liệu `iot_processed` chỉ cập nhật khi chạy lại script tiền xử lý.

**Báo cáo Word và bộ ảnh minh chứng đầy đủ sẽ bổ sung sau khi chụp ảnh thực nghiệm.**
