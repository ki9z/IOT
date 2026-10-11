"""IoT Lab2 / Bước 5 - Streamlit + MongoDB Atlas Dashboard.

Run in the SAME directory as the working .env from Bước 2:
    python -m streamlit run dashboard_iot.py

Dashboard is read-only. It NEVER inserts/updates/deletes documents.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import certifi
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from bson import BSON

from dashboard_metrics import (
    SENSORS, make_frame, make_local, median_delivery_offset_ms,
    received_period_ms, sequence_audit,
)

load_dotenv()

st.set_page_config(
    page_title="IoT Lab 2 | Dashboard",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

MONGO_URI = os.getenv("MONGO_URI", "").strip()
DB_NAME = os.getenv("MONGO_DB", "iot_lab2").strip()
RAW_NAME = os.getenv("MONGO_COLLECTION", "iot_raw").strip()
PROCESSED_NAME = os.getenv("MONGO_PROCESSED_COLLECTION", "iot_processed").strip()
WRITE_METRICS_NAME = os.getenv("MONGO_WRITE_METRICS_COLLECTION", "iot_write_metrics").strip()
LOCAL_TZ = "Asia/Ho_Chi_Minh"
MAX_ROWS = 12000


@st.cache_resource(show_spinner=False)
def mongo_client(uri: str):
    return MongoClient(
        uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=12000,
        tz_aware=True,
        tzinfo=timezone.utc,
    )


@st.cache_data(ttl=8, show_spinner=False)
def find_devices():
    client = mongo_client(MONGO_URI)
    db = client[DB_NAME]
    # Trong bài thực hành, ít thiết bị nên distinct rất nhẹ.
    values = db[RAW_NAME].distinct("metadata.device_id")
    return sorted(str(d) for d in values if isinstance(d, str) and d.strip())


@st.cache_data(ttl=8, show_spinner=False)
def read_data(device_id: str, hours: int, window_seconds: int):
    db = mongo_client(MONGO_URI)[DB_NAME]
    device_filter = {"metadata.device_id": device_id}
    last = db[RAW_NAME].find_one(device_filter, {"time": 1}, sort=[("time", DESCENDING)])
    if last is None:
        return [], [], {}, False
    newest = last["time"]
    earliest = newest - timedelta(hours=hours)
    filter_raw = {**device_filter, "time": {"$gte": earliest, "$lte": newest}}
    raw_projection = {"_id": 0, "time": 1, "received_at": 1, "metadata": 1,
                      "sequence": 1, "uptime_s": 1, "temperature": 1,
                      "humidity": 1, "distance_cm": 1, "rssi": 1, "latency_ms": 1}
    # Lấy điểm mới nhất nếu lượng dữ liệu nhiều; đảo chiều sau để vẽ thời gian tăng dần.
    raws = list(db[RAW_NAME].find(filter_raw, raw_projection)
                .sort("time", DESCENDING).limit(MAX_ROWS + 1))
    truncated = len(raws) > MAX_ROWS
    if truncated:
        raws = raws[:MAX_ROWS]
    raws.reverse()
    proc_filter = {**device_filter,
                   "time": {"$gte": earliest - timedelta(seconds=window_seconds),
                            "$lte": newest},
                   "window_seconds": window_seconds}
    processed = list(db[PROCESSED_NAME].find(proc_filter, {"_id": 0, "metadata": 0})
                     .sort("time", DESCENDING).limit(MAX_ROWS))
    processed.reverse()
    # Thống kê tổng collection: có thể bị giới hạn quyền ở Atlas, không làm app dừng.
    counts = {}
    try:
        counts["raw"] = db[RAW_NAME].count_documents(device_filter)
        counts["processed"] = db[PROCESSED_NAME].count_documents(device_filter)
    except Exception:
        pass
    return raws, processed, counts, truncated


@st.cache_data(ttl=8, show_spinner=False)
def read_write_metrics(device: str, hours: int):
    client = mongo_client(MONGO_URI)
    coll = client[DB_NAME][WRITE_METRICS_NAME]
    device_filter = {"metadata.device_id": device}
    latest = coll.find_one(device_filter, {"stored_at": 1}, sort=[("stored_at", DESCENDING)])
    if latest is None:
        return []
    after = latest["stored_at"] - timedelta(hours=hours)
    return list(coll.find(
        {**device_filter, "stored_at": {"$gte": after}},
        {"_id": 0, "stored_at": 1, "mongodb_insert_ms": 1,
         "collector_to_db_ms": 1}
    ).sort("stored_at", DESCENDING).limit(MAX_ROWS))


def plot_line(data: pd.DataFrame, key: str, label: str, color: str):
    fig = go.Figure()
    if not data.empty and key in data:
        fig.add_trace(go.Scatter(
            x=data["time_local"], y=data[key], mode="lines+markers",
            name=label, line=dict(color=color, width=2), marker=dict(size=4),
            connectgaps=False,
        ))
    fig.update_layout(
        margin=dict(l=8, r=8, t=26, b=8), height=260,
        xaxis_title="Giờ Việt Nam", yaxis_title=label,
        hovermode="x unified",
    )
    return fig


def compare_plot(raw: pd.DataFrame, processed: pd.DataFrame, sensor: str):
    fig = go.Figure()
    if not raw.empty and sensor in raw:
        fig.add_trace(go.Scatter(x=raw["time_local"], y=raw[sensor],
                                 name="Dữ liệu gốc", mode="lines",
                                 line=dict(color="#94a3b8", width=1)))
    if not processed.empty and sensor in processed:
        fig.add_trace(go.Scatter(x=processed["time_local"], y=processed[sensor],
                                 name="Trung bình sau xử lý", mode="lines+markers",
                                 line=dict(color="#2563eb", width=3)))
    roll = sensor + "_rolling_mean_3"
    if not processed.empty and roll in processed:
        fig.add_trace(go.Scatter(x=processed["time_local"], y=processed[roll],
                                 name="Rolling mean (3 cửa sổ)", mode="lines",
                                 line=dict(color="#e8790c", dash="dash")))
    fig.update_layout(height=360, margin=dict(l=8, r=8, t=25, b=6),
                      xaxis_title="Giờ Việt Nam", yaxis_title=sensor,
                      legend=dict(orientation="h", y=1.15), hovermode="x unified")
    return fig


def fmt(v, suffix="", decimals=1):
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{decimals}f}{suffix}"


def show_times(time_obj):
    if time_obj is None:
        return "—"
    return pd.Timestamp(time_obj).tz_convert(LOCAL_TZ).strftime("%d/%m/%Y %H:%M:%S")


def run_dashboard():
    try:
        raws, processeds, counts, truncated = read_data(selected_device, hours, window_seconds)
    except Exception as exc:
        st.error("Không đọc được MongoDB Atlas. Kiểm tra kết nối Internet, IP Network Access và quyền đọc collection.")
        st.caption(f"Loại lỗi: {type(exc).__name__} (không hiển thị chuỗi kết nối hay mật khẩu).")
        return

    raw = make_local(make_frame(raws))
    processed = make_local(make_frame(processeds, processed=True))
    if raw.empty:
        st.warning("iot_raw chưa có dữ liệu cho thiết bị đã chọn. Hãy chạy Wokwi và chương trình MQTT collector.")
        return

    if truncated:
        st.warning(f"Có hơn {MAX_ROWS:,} bản ghi trong khoảng chọn. Chỉ hiển thị {MAX_ROWS:,} bản ghi mới nhất để tránh chậm máy trường.")

    last = raw.iloc[-1]
    local_time = show_times(last["time"])
    st.caption(f"Thiết bị: **{selected_device}** · Bản ghi mới nhất (giờ Việt Nam): **{local_time}** · "
               f"Dữ liệu gốc: **{len(raw):,}** mẫu · Dữ liệu xử lý: **{len(processed):,}** cửa sổ")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🌡️ Nhiệt độ", fmt(last.get("temperature"), " °C"))
    c2.metric("💧 Độ ẩm", fmt(last.get("humidity"), " %"))
    c3.metric("📏 Khoảng cách", fmt(last.get("distance_cm"), " cm"))
    c4.metric("📶 WiFi RSSI", fmt(last.get("rssi"), " dBm", 0))

    # Chênh lệch đồng hồ/số đo, tránh coi nhầm là network latency thuần.
    lag = median_delivery_offset_ms(raw)
    if lag is not None and (lag > 5000 or lag < -1000):
        st.warning(
            "Chênh lệch giữa timestamp ESP32 và thời điểm Python nhận bản tin đang lớn. "
            "Giá trị này chứa sai lệch đồng hồ, không thể kết luận là độ trễ mạng MQTT. "
            "Bạn từng thấy khoảng 71 giây; hãy kiểm tra đồng bộ NTP khi viết báo cáo."
        )

    tab_live, tab_proc, tab_quality, tab_table = st.tabs([
        "📈 Giám sát cảm biến", "🧪 Dữ liệu đã xử lý", "🕒 Chất lượng và độ trễ", "📋 Bảng dữ liệu"
    ])

    with tab_live:
        a, b = st.columns(2)
        with a:
            st.subheader("Nhiệt độ")
            st.plotly_chart(plot_line(raw, "temperature", "°C", "#f97316"), use_container_width=True)
            st.subheader("Khoảng cách")
            st.plotly_chart(plot_line(raw, "distance_cm", "cm", "#22c55e"), use_container_width=True)
        with b:
            st.subheader("Độ ẩm")
            st.plotly_chart(plot_line(raw, "humidity", "%", "#3b82f6"), use_container_width=True)
            st.subheader("Tín hiệu WiFi")
            st.plotly_chart(plot_line(raw, "rssi", "dBm", "#8b5cf6"), use_container_width=True)

    with tab_proc:
        if processed.empty:
            st.info("Chưa tìm thấy dữ liệu trong iot_processed cho khoảng và cửa sổ đã chọn. "
                    "Hãy chạy `python preprocess_iot.py --hours 6 --window-seconds 30`, "
                    "sau đó nhấn tải lại Dashboard.")
        else:
            sensor = st.selectbox("Chọn cảm biến để so sánh", list(SENSORS),
                                  format_func=lambda x: {"temperature":"Nhiệt độ (°C)",
                                                       "humidity":"Độ ẩm (%)",
                                                       "distance_cm":"Khoảng cách (cm)"}[x])
            st.plotly_chart(compare_plot(raw, processed, sensor), use_container_width=True)
            x, y = st.columns(2)
            with x:
                st.subheader("Chuẩn hóa Min–Max [0,1]")
                st.plotly_chart(plot_line(processed, sensor + "_norm", "Chuẩn hóa", "#16a34a"),
                                use_container_width=True)
            with y:
                st.subheader("Delta giữa hai cửa sổ")
                st.plotly_chart(plot_line(processed, sensor + "_delta", "Delta", "#f59e0b"),
                                use_container_width=True)
            last_proc = processed.iloc[-1]
            st.caption("Dữ liệu xử lý gần nhất: " + show_times(last_proc["time"]) +
                       ". Kết quả chỉ thay đổi khi chạy lại preprocess_iot.py; "
                       "không tự động tính lại theo MQTT.")

    with tab_quality:
        audit = sequence_audit(raw)
        period = received_period_ms(raw)
        a, b, c, d = st.columns(4)
        a.metric("Nghi thiếu sequence", f"{audit['suspected_missing']:,}")
        b.metric("Nghi khởi động lại", f"{audit['reboot_hints']:,}")
        c.metric("Khoảng nhận trung vị", fmt(period, " ms", 0))
        d.metric("Chênh lệch thời gian trung vị", fmt(lag, " ms", 0))
        st.caption("Chênh lệch thời gian = received_at − time; có thể bao gồm sai lệch đồng hồ, "
                   "không được coi là độ trễ MQTT/end-to-end. Số mẫu thiếu chỉ là ước lượng theo sequence.")

        st.subheader("Hiệu năng Python → MongoDB (đo thực tế)")
        try:
            write_rows = read_write_metrics(selected_device, hours)
        except Exception as exc:
            write_rows = []
            st.warning("Chưa đọc được iot_write_metrics: " + type(exc).__name__)
        if write_rows:
            wdf = pd.DataFrame(write_rows)
            insert_values = pd.to_numeric(wdf["mongodb_insert_ms"], errors="coerce").dropna()
            callback_values = pd.to_numeric(wdf["collector_to_db_ms"], errors="coerce").dropna()
            ma, mb, mc = st.columns(3)
            ma.metric("Mẫu đo thành công", f"{len(wdf):,}")
            mb.metric("Trung vị insert_one()", fmt(insert_values.median() if not insert_values.empty else None, " ms", 2))
            mc.metric("Trung vị callback → DB", fmt(callback_values.median() if not callback_values.empty else None, " ms", 2))
            st.caption("Hai giá trị này đo bằng đồng hồ đơn điệu perf_counter_ns() tại Python. "
                       "Chúng không bao gồm thời gian ESP32 → MQTT Broker → Python. "
                       "Đo end-to-end thực cần đồng bộ và kiểm chứng đồng hồ hai thiết bị.")
        else:
            st.info("Chưa có phép đo ghi MongoDB. Chạy mqtt_to_mongodb_v2.py phiên bản đã cập nhật "
                    "trong vài phút, sau đó làm mới trang.")
        if not processed.empty:
            a, b, c = st.columns(3)
            a.metric("Cửa sổ chưa có bản tin", int(processed.get("was_missing_window", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()))
            b.metric("Số mẫu đã gộp", int(processed.get("sample_count", pd.Series(dtype=float)).fillna(0).sum()))
            c.metric("Outlier đã đánh dấu", int(processed.get("outlier_count", pd.Series(dtype=float)).fillna(0).sum()))
        st.subheader("Đánh giá lưu trữ")
        aa, bb = st.columns(2)
        aa.metric("Bản ghi gốc của thiết bị (toàn thời gian)", f"{counts.get('raw', len(raw)):,}")
        bb.metric("Cửa sổ đã xử lý của thiết bị (toàn thời gian)", f"{counts.get('processed', len(processed)):,}")
        sample_bytes = [len(BSON.encode(d)) for d in raws[-min(100, len(raws)):]]
        estimated = (sum(sample_bytes) / len(sample_bytes)) if sample_bytes else 0
        st.write(f"Kích thước BSON trung bình của mẫu gốc (ước tính từ tối đa 100 mẫu): **{estimated:.0f} bytes/bản ghi**.")
        st.caption("Đây chỉ là dung lượng BSON ước tính, không bao gồm toàn bộ overhead, chỉ mục, "
                   "nén hay dung lượng lưu trữ thực tế trên Atlas. Thời hạn lưu tùy cấu hình TTL.")
        st.subheader("Thống kê chênh lệch nhận")
        if "latency_ms" in raw:
            lag_df = raw[["time_local", "latency_ms"]].dropna()
            if not lag_df.empty:
                st.plotly_chart(plot_line(raw, "latency_ms", "ms", "#dc2626"), use_container_width=True)

    with tab_table:
        st.subheader("Bản ghi thô gần nhất")
        raw_cols = [c for c in ("time_local", "received_local", "temperature", "humidity",
                                 "distance_cm", "rssi", "sequence", "uptime_s", "latency_ms") if c in raw]
        st.dataframe(raw[raw_cols].tail(100).sort_values("time_local", ascending=False),
                     use_container_width=True, hide_index=True)
        st.download_button("Tải CSV dữ liệu gốc", raw[raw_cols].to_csv(index=False).encode("utf-8-sig"),
                           file_name="iot_raw_dashboard.csv", mime="text/csv")
        if not processed.empty:
            st.subheader("Dữ liệu tiền xử lý")
            processed_cols = [c for c in ("time_local", "temperature", "humidity", "distance_cm",
                                          "temperature_rolling_mean_3", "temperature_delta",
                                          "temperature_norm", "sample_count", "outlier_count",
                                          "was_missing_window") if c in processed]
            st.dataframe(processed[processed_cols].tail(100).sort_values("time_local", ascending=False),
                         use_container_width=True, hide_index=True)
            st.download_button("Tải CSV dữ liệu xử lý",
                               processed[processed_cols].to_csv(index=False).encode("utf-8-sig"),
                               file_name="iot_processed_dashboard.csv", mime="text/csv")

    st.caption("Dashboard chỉ đọc dữ liệu. Không cập nhật ESP32, MQTT hoặc MongoDB. "
               "Muốn đóng: Ctrl + C tại cửa sổ chạy Streamlit.")


st.title("📡 IoT Lab 2 – Dashboard giám sát cảm biến")
st.write("**ESP32 Wokwi → HiveMQ → Python → MongoDB Atlas → Streamlit**")

if not MONGO_URI or "<" in MONGO_URI or ">" in MONGO_URI:
    st.error("Chưa có MONGO_URI hợp lệ. Hãy đặt dashboard_iot.py và .env vào cùng một thư mục.")
    st.stop()

try:
    mongo_client(MONGO_URI).admin.command("ping")
    devices = find_devices()
except Exception as exc:
    st.error("Không kết nối được MongoDB Atlas. Kiểm tra Internet, quyền IP Network Access hoặc .env.")
    st.caption(f"Loại lỗi: {type(exc).__name__}; không chia sẻ thông tin mật khẩu.")
    st.stop()
if not devices:
    st.warning("Chưa tìm thấy thiết bị. Cần lưu dữ liệu vào iot_raw trước khi mở Dashboard.")
    st.stop()

with st.sidebar:
    st.header("⚙️ Bộ lọc")
    selected_device = st.selectbox("Thiết bị", devices)
    hours = st.selectbox("Khoảng dữ liệu gần nhất", [1, 3, 6, 12, 24], index=2,
                         format_func=lambda h: f"{h} giờ (tính từ bản ghi mới nhất)")
    window_seconds = st.selectbox("Resampling đã xử lý", [30, 15, 60, 300],
                                  format_func=lambda s: f"{s} giây")
    auto_refresh = st.checkbox("Tự làm mới mỗi 10 giây", value=True)
    if st.button("🔄 Làm mới ngay"):
        read_data.clear()
        find_devices.clear()
        read_write_metrics.clear()
    st.caption("Đừng tắt PowerShell đang chạy MQTT collector. Có thể xem dữ liệu cũ khi Wokwi ngừng.")

# Chỉ fragment chạy lại, tránh reload toàn bộ trang mỗi 10 giây.
@st.fragment(run_every="10s" if auto_refresh else None)
def live_content():
    run_dashboard()

live_content()
