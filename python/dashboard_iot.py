"""IoT Lab 2 application. Run: python -m streamlit run python/dashboard_iot.py.

Reads real Atlas telemetry by default. Demo is explicit and entirely offline.
Processed writes require DASHBOARD_ALLOW_WRITES=true and an explicit UI action.
"""
from __future__ import annotations

import html
import inspect
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import certifi
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from bson import BSON
from dotenv import load_dotenv
from pymongo import MongoClient

from dashboard_data import MAX_ROWS, demo_snapshot, read_snapshot
from dashboard_metrics import (SENSORS, freshness, make_frame, make_local,
                               median_delivery_offset_ms, numeric_summary,
                               quality_table, received_period_ms, sequence_audit)
from preprocess_iot import save_processed, to_docs_for_device

load_dotenv(Path(__file__).with_name(".env"))
MONGO_URI = os.getenv("MONGO_URI", "").strip()
DB_NAME = os.getenv("MONGO_DB", "iot_lab2").strip()
RAW_NAME = os.getenv("MONGO_COLLECTION", "iot_raw").strip()
PROCESSED_NAME = os.getenv("MONGO_PROCESSED_COLLECTION", "iot_processed").strip()
METRICS_NAME = os.getenv("MONGO_WRITE_METRICS_COLLECTION", "iot_write_metrics").strip()
ALLOW_WRITES = os.getenv("DASHBOARD_ALLOW_WRITES", "false").lower() == "true"
LOCAL_TZ = "Asia/Ho_Chi_Minh"
SENSOR_INFO = {"temperature": ("Nhiệt độ", "°C", "#e17b41"),
               "humidity": ("Độ ẩm", "%", "#4286d4"),
               "distance_cm": ("Khoảng cách", "cm", "#1b9a89"),
               "rssi": ("Tín hiệu WiFi", "dBm", "#8874c8")}

st.set_page_config(page_title="IoT Lab · Sensor Studio", page_icon="📡", layout="wide")
st.markdown("""<style>
.stApp {background:#f5f7fb; color:#192a43;}
.block-container {max-width:1540px; padding-top:2rem; padding-bottom:2rem;}
[data-testid="stSidebar"] {background:#ffffff; border-right:1px solid #e5eaf2;}
h1,h2,h3 {letter-spacing:-.035em; color:#192a43;}
h1 {font-size:2.4rem !important;} h3 {font-size:1.15rem !important;}
[data-testid="stMetric"] {background:white; border:1px solid #e5eaf2;
  border-radius:14px; padding:18px 20px; box-shadow:0 3px 12px #1c355006;}
[data-testid="stMetricLabel"] {color:#607088; font-size:.85rem;}
[data-testid="stMetricValue"] {font-weight:650; font-size:1.9rem;}
[data-testid="stVerticalBlockBorderWrapper"] > div {border-color:#e5eaf2 !important;
  border-radius:14px !important; background:white;}
.stTabs [data-baseweb="tab-list"] {gap:22px; border-bottom:1px solid #dce3ef;}
.stTabs [data-baseweb="tab"] {font-weight:600; padding:14px 0;}
.stTabs [aria-selected="true"] {color:#237a72 !important;}
.stTabs [data-baseweb="tab-highlight"] {background:#237a72;}
.stButton button[kind="primary"] {background:#237a72; border-color:#237a72;}
.eyebrow {font-size:12px; font-weight:700; letter-spacing:.14em; color:#237a72; margin-bottom:8px;}
.hero-note {color:#68788f; margin-top:-10px; margin-bottom:22px; font-size:15px;}
@media (max-width:700px) {.block-container {padding:1rem;} h1 {font-size:1.8rem !important;}}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def mongo_client(uri):
    return MongoClient(uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=6000,
                       connectTimeoutMS=6000, socketTimeoutMS=10000,
                       tz_aware=True, tzinfo=timezone.utc)


@st.cache_data(ttl=8, show_spinner=False)
def find_devices():
    db = mongo_client(MONGO_URI)[DB_NAME]
    db.client.admin.command("ping")
    return sorted(d for d in db[RAW_NAME].distinct("metadata.device_id")
                  if isinstance(d, str) and d.strip())


@st.cache_data(ttl=8, show_spinner=False)
def read_data(device, minutes, window, historical, demo):
    if demo:
        return demo_snapshot(minutes, window)
    return read_snapshot(mongo_client(MONGO_URI)[DB_NAME], RAW_NAME, PROCESSED_NAME,
                         METRICS_NAME, device, minutes, window, historical)


def fmt(value, suffix="", decimals=1):
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.{decimals}f}{(' ' + suffix) if suffix else ''}"


def show_time(value):
    if value is None or pd.isna(value):
        return "—"
    return pd.to_datetime(value, utc=True).tz_convert(LOCAL_TZ).strftime("%d/%m/%Y %H:%M:%S")


def base_figure(unit, height=260):
    fig = go.Figure()
    fig.update_layout(template="plotly_white", height=height,
                      margin=dict(l=12, r=12, t=15, b=12),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Arial, sans-serif", size=12, color="#6b7b92"),
                      hovermode="x unified", yaxis_title=unit,
                      legend=dict(orientation="h", y=1.16, x=0),
                      xaxis=dict(showgrid=False, tickformat="%H:%M", title="Giờ Việt Nam"),
                      yaxis=dict(gridcolor="#edf1f7", zerolinecolor="#edf1f7"))
    return fig


def line_plot(data, key, unit, color, height=260):
    fig = base_figure(unit, height)
    if not data.empty and key in data:
        fig.add_trace(go.Scatter(x=data["time_local"], y=data[key], mode="lines",
                                 name=unit, line=dict(color=color, width=2), connectgaps=False))
    return fig


def chart(fig, key):
    # Plotly gained width later than native widgets; keep 1.50 compatibility.
    sizing = ({"width": "stretch"} if "width" in inspect.signature(st.plotly_chart).parameters
              else {"use_container_width": True})
    st.plotly_chart(fig, **sizing, key=key,
                    config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})


def compare_plot(raw, processed, sensor):
    label, unit, _ = SENSOR_INFO[sensor]
    fig = base_figure(unit, 350)
    if sensor in raw:
        fig.add_trace(go.Scatter(x=raw.time_local, y=raw[sensor], name="Dữ liệu gốc",
                                 mode="lines", line=dict(color="#acb9c9", width=1.4), connectgaps=False))
    if sensor in processed:
        fig.add_trace(go.Scatter(x=processed.time_local, y=processed[sensor],
                                 name="Sau xử lý", mode="lines+markers",
                                 line=dict(color="#237a72", width=2.5), marker=dict(size=5), connectgaps=False))
    rolling = sensor + "_rolling_mean_3"
    if rolling in processed:
        fig.add_trace(go.Scatter(x=processed.time_local, y=processed[rolling],
                                 name="Rolling mean · 3 cửa sổ", mode="lines",
                                 line=dict(color="#e3a251", dash="dash", width=2), connectgaps=False))
    return fig


def preprocessing_tab(snapshot, raw, processed, context, demo):
    st.subheader("Từ bản tin cảm biến đến dữ liệu có thể phân tích")
    st.caption("Loại trùng → kiểm tra miền giá trị → IQR (1,5×) → trung bình theo cửa sổ → nội suy ngắn → rolling / delta / Min–Max.")
    a, b, c = st.columns(3)
    a.metric("Mẫu gốc trong khoảng chọn", fmt(len(raw), decimals=0))
    b.metric("Cửa sổ đã lưu" if not demo else "Cửa sổ demo", fmt(len(processed), decimals=0))
    c.metric("Độ rộng cửa sổ", f"{window_seconds} giây")
    st.info("IQR có thể đánh dấu cả thay đổi bạn chủ động tạo trong Wokwi. Kiểm tra biểu đồ gốc trước khi diễn giải đó là lỗi cảm biến.")
    with st.container(border=True):
        st.markdown("**Chạy pipeline với dữ liệu đang chọn**")
        preview_col, save_col = st.columns(2)
        preview_clicked = preview_col.button("Xem trước tiền xử lý", key="preview", width="stretch",
                                              disabled=snapshot["truncated"])
        save_clicked = save_col.button("Chạy và lưu kết quả", key="save", type="primary",
                                       width="stretch",
                                       disabled=demo or not ALLOW_WRITES or snapshot["truncated"])
        if demo:
            st.caption("Chế độ demo: chỉ tính toán trong bộ nhớ; các phép đo và cảm biến đều là dữ liệu mô phỏng.")
        elif not ALLOW_WRITES:
            st.caption("Để lưu từ app, đặt DASHBOARD_ALLOW_WRITES=true trong .env và khởi động lại. Hoặc chạy preprocess_iot.py từ terminal.")
        else:
            st.caption(f"Nút lưu cập nhật cửa sổ trong {PROCESSED_NAME}; giữ nguyên collection dữ liệu gốc. Chỉ chạy một tiến trình tiền xử lý tại một thời điểm.")
        if preview_clicked or save_clicked:
            try:
                with st.spinner("Đang xử lý các bản ghi trong khoảng chọn…"):
                    docs, stats = to_docs_for_device(snapshot["raw"], selected_device, window_seconds)
                    if not docs:
                        raise ValueError("No usable samples")
                    if save_clicked:
                        result = save_processed(mongo_client(MONGO_URI)[DB_NAME], docs,
                                                RAW_NAME, PROCESSED_NAME, METRICS_NAME)
                        st.session_state["save_notice"] = (context, result)
                        read_data.clear()
                    st.session_state["preview_result"] = dict(context=context, docs=docs, stats=stats,
                                                              end=snapshot["end"], saved=save_clicked)
                if save_clicked:
                    st.rerun(scope="app")
            except Exception as exc:
                st.error(f"Không hoàn tất tiền xử lý ({type(exc).__name__}). Kiểm tra quyền ghi, cấu hình collection và khoảng thời gian; dữ liệu gốc được giữ nguyên.")
        notice = st.session_state.get("save_notice")
        if notice and notice[0] == context:
            result = notice[1]
            st.success(f"Lần lưu gần nhất: {result['upserted']} cửa sổ mới, {result['modified']} cửa sổ cập nhật.")
        preview = st.session_state.get("preview_result")
        if preview and preview["context"] == context:
            stats = preview["stats"]
            st.caption("Kết quả lần chạy tại mốc " + show_time(preview["end"]) + "; chưa tự tính lại khi có bản tin mới.")
            cols = st.columns(4)
            for col, label, value in zip(cols, ["Trùng bị loại", "Giá trị thiếu / ngoài miền", "Outlier IQR", "Cửa sổ rỗng"],
                                         [stats["duplicates_dropped"], stats["invalid_sensor_values"], stats["outliers_detected"], stats["missing_windows"]]):
                col.metric(label, fmt(value, decimals=0))
            with st.expander("Chi tiết từng cảm biến và xem trước kết quả", expanded=False):
                detail = pd.DataFrame([{"Cảm biến": SENSOR_INFO[s][0],
                                        "Thiếu / ngoài miền": stats["invalid_by_sensor"][s],
                                        "Outlier": stats["outliers_by_sensor"][s],
                                        "Cửa sổ nội suy": stats["filled_by_sensor"][s],
                                        "Cửa sổ còn thiếu": stats["remaining_missing_by_sensor"][s]} for s in SENSORS])
                st.dataframe(detail, hide_index=True, width="stretch")
                pframe = make_local(make_frame(preview["docs"], processed=True))
                st.dataframe(pframe.drop(columns=["metadata"], errors="ignore"), hide_index=True,
                             width="stretch")
                st.download_button("Tải thống kê lần chạy (JSON)", json.dumps(stats, ensure_ascii=False, indent=2),
                                   "preprocessing_stats.json", "application/json", key="stats_download")
    sensor = st.selectbox("Cảm biến so sánh", SENSORS, format_func=lambda x: SENSOR_INFO[x][0], key="compare_sensor")
    if processed.empty:
        st.info("Chưa có kết quả đã lưu cho cửa sổ này. Dùng xem trước để kiểm tra, sau đó lưu từ app hoặc CLI.")
        return
    with st.container(border=True):
        st.markdown("**Dữ liệu gốc và kết quả đã lưu**")
        chart(compare_plot(raw, processed, sensor), "comparison")
        if "processed_at" in processed:
            st.caption("Lần xử lý mới nhất: " + show_time(processed.processed_at.max()) +
                       ". Khi dữ liệu gốc thay đổi, cần chạy lại tiền xử lý.")
    x, y = st.columns(2)
    with x, st.container(border=True):
        st.markdown("**Chuẩn hóa Min–Max**")
        chart(line_plot(processed, sensor+"_norm", "[0, 1]", "#237a72"), "normalization")
        st.caption("Min và max lấy trong khoảng của lần chạy; không so sánh trực tiếp giữa các lần chạy khác nhau.")
    with y, st.container(border=True):
        st.markdown("**Delta giữa hai cửa sổ**")
        chart(line_plot(processed, sensor+"_delta", SENSOR_INFO[sensor][1], "#e3a251"), "delta")
        st.caption("Giá trị dương: tăng; âm: giảm. Delta tại khoảng thiếu vẫn để trống.")


def quality_tab(snapshot, raw, processed):
    audit = sequence_audit(raw)
    quality = quality_table(raw)
    cols = st.columns(4)
    for col, label, value in zip(cols, ["Bản ghi nghi trùng", "Sequence nghi thiếu", "Dấu hiệu khởi động lại", "Chu kỳ nhận trung vị"],
                                 [fmt(audit["duplicates"], decimals=0), fmt(audit["suspected_missing"], decimals=0),
                                  fmt(audit["reboot_hints"], decimals=0), fmt(received_period_ms(raw), "ms", 0)]):
        col.metric(label, value)
    st.caption("Sequence thiếu là dấu hiệu cần kiểm tra; không chứng minh mất gói do mạng. Bản ghi bị collector từ chối không nằm trong thống kê này.")
    a, b = st.columns([1, 1])
    with a, st.container(border=True):
        st.markdown("**Chất lượng dữ liệu đã lưu**")
        display = quality.copy()
        display["Cảm biến"] = display["Cảm biến"].map(lambda x: SENSOR_INFO[x][0])
        st.dataframe(display, hide_index=True, width="stretch")
    with b, st.container(border=True):
        st.markdown("**Lưu trữ trong khoảng chọn**")
        sizes = [len(BSON.encode(row)) for row in snapshot["raw"][-100:]]
        st.metric("BSON trung bình · tối đa 100 mẫu", fmt(sum(sizes)/len(sizes) if sizes else None, "bytes", 0))
        st.caption("Ước tính theo các trường dashboard đã đọc, không gồm _id, chỉ mục, nén và overhead Atlas.")
        if not processed.empty:
            st.write(f"**{len(processed):,}** cửa sổ · **{int(processed.get('sample_count', pd.Series(dtype=float)).sum()):,}** mẫu gộp · **{int(processed.get('outlier_count', pd.Series(dtype=float)).sum()):,}** giá trị outlier.")
    st.subheader("Hiệu năng ghi từ Python tới MongoDB")
    if snapshot["metrics_error"]:
        st.warning("Không đọc được collection phép đo: " + snapshot["metrics_error"])
    wdf = pd.DataFrame(snapshot["metrics"])
    summary = {}
    for key, label in [("mongodb_insert_ms", "MongoDB insert"), ("collector_to_db_ms", "Callback → DB")]:
        summary[key] = numeric_summary(wdf.get(key, pd.Series(dtype=float)))
    report = [{"Phép đo": label, "Mẫu hợp lệ": summary[key]["count"],
               "Trung bình (ms)": summary[key]["mean"], "P95 (ms)": summary[key]["p95"],
               "Min (ms)": summary[key]["min"], "Max (ms)": summary[key]["max"]}
              for key, label in [("mongodb_insert_ms", "MongoDB insert"), ("collector_to_db_ms", "Callback → DB")]]
    if not wdf.empty:
        a, b, c = st.columns(3)
        a.metric("Phép đo trong khoảng chọn", fmt(len(wdf), decimals=0))
        b.metric("Callback → DB trung bình", fmt(summary["collector_to_db_ms"]["mean"], "ms", 2))
        c.metric("Callback → DB P95", fmt(summary["collector_to_db_ms"]["p95"], "ms", 2))
        st.dataframe(pd.DataFrame(report), hide_index=True, width="stretch")
        left, right = st.columns(2)
        vals = pd.to_numeric(wdf.get("collector_to_db_ms", pd.Series(dtype=float)), errors="coerce")
        vals = vals[vals.notna() & vals.ge(0) & vals.lt(float("inf"))]
        with left, st.container(border=True):
            st.markdown("**Độ trễ theo thời gian nhận xác nhận**")
            times = pd.to_datetime(wdf.get("stored_at", pd.Series(index=wdf.index, dtype=object)), utc=True, errors="coerce")
            tdf = pd.DataFrame({"time_local": times.dt.tz_convert(LOCAL_TZ), "value": vals}).sort_values("time_local")
            chart(line_plot(tdf, "value", "ms", "#237a72"), "write_latency")
        with right, st.container(border=True):
            st.markdown("**Phân bố callback → DB**")
            fig = base_figure("Số phép đo")
            fig.update_layout(xaxis_title="Thời gian (ms)", xaxis_tickformat=None, hovermode="closest")
            fig.add_trace(go.Histogram(x=vals, nbinsx=20, marker_color="#4286d4", name="Phép đo"))
            chart(fig, "histogram")
    else:
        st.info("Chưa có phép đo cho khoảng chọn. Chạy mqtt_to_mongodb_v2.py và thu thêm bản tin.")
    st.caption("Đo bằng perf_counter_ns() trên một đồng hồ Python. Không bao gồm đường truyền ESP32 → broker → Python; đây chưa phải độ trễ end-to-end đã kiểm chứng.")
    with st.expander("Chênh lệch đồng hồ thiết bị và máy thu thập"):
        st.metric("received_at − time · trung vị", fmt(median_delivery_offset_ms(raw), "ms", 0))
        st.caption("Chênh lệch chứa cả thời gian truyền và sai lệch đồng hồ. Cần đồng bộ và kiểm chứng đồng hồ để đáp ứng phép đo end-to-end của đề.")
        if "received_at" in raw:
            offsets = raw.copy()
            offsets["offset"] = (offsets.received_at - offsets.time).dt.total_seconds()*1000
            chart(line_plot(offsets, "offset", "ms", "#8874c8"), "offset")
    payload = {"mode": "synthetic_demo" if demo_mode else "mongodb", "device_id": selected_device,
               "range_start_utc": snapshot["start"].isoformat(), "range_end_utc": snapshot["end"].isoformat(),
               "raw_rows": len(raw), "processed_rows": len(processed),
               "raw_truncated": snapshot["truncated"], "metrics_truncated": snapshot["metrics_truncated"],
               "sequence_audit": audit, "stored_reading_quality": quality.to_dict("records"),
               "write_duration_ms": summary, "verified_end_to_end": False}
    st.download_button("Tải số liệu đánh giá (JSON)", json.dumps(payload, ensure_ascii=False, indent=2),
                       "dashboard_evaluation.json", "application/json", key="evaluation_download")


def table_tab(raw, processed):
    st.subheader("Khám phá và xuất dữ liệu")
    source = st.radio("Nguồn dữ liệu", ["Dữ liệu gốc", "Dữ liệu đã xử lý"], horizontal=True, key="table_source")
    data = (raw if source == "Dữ liệu gốc" else processed).copy()
    if data.empty:
        st.info("Không có bản ghi trong nguồn và khoảng đã chọn.")
        return
    if "metadata" in data:
        data["device_id"] = data.metadata.map(lambda d: d.get("device_id") if isinstance(d, dict) else None)
        data = data.drop(columns="metadata")
    available = list(data.columns)
    default = [c for c in ["time_local", "device_id", "temperature", "humidity", "distance_cm", "rssi",
                           "sequence", "uptime_s", "sample_count", "outlier_count"] if c in data]
    columns = st.multiselect("Cột hiển thị và xuất CSV", available, default=default, key="table_columns_"+source)
    if not columns:
        st.info("Chọn ít nhất một cột để xem dữ liệu.")
        return
    rows_per_page = st.selectbox("Số dòng mỗi trang", [25, 50, 100], index=1, key="page_size")
    newest_first = st.checkbox("Mới nhất trước", value=True, key="table_desc")
    data = data.sort_values("time", ascending=not newest_first)
    total_pages = (len(data)+rows_per_page-1)//rows_per_page
    page = st.selectbox("Trang", list(range(1, total_pages+1)), key=f"page_{source}_{rows_per_page}_{total_pages}")
    start = (page-1)*rows_per_page
    st.caption(f"{len(data):,} dòng trong khoảng chọn · trang {page}/{total_pages}")
    st.dataframe(data[columns].iloc[start:start+rows_per_page], hide_index=True, width="stretch")
    st.download_button("Xuất toàn bộ dữ liệu đang lọc (CSV)", data[columns].to_csv(index=False).encode("utf-8-sig"),
                       "iot_raw.csv" if source == "Dữ liệu gốc" else "iot_processed.csv", "text/csv", key="csv_download")
    st.caption("CSV gồm toàn bộ khoảng đã tải và các cột chọn, không chỉ trang đang xem. Giờ Việt Nam có offset +07:00.")


def run_dashboard():
    try:
        snapshot = read_data(selected_device, minutes, window_seconds, historical, demo_mode)
    except Exception as exc:
        st.error(f"Không đọc được dữ liệu ({type(exc).__name__}). Kiểm tra Internet, Atlas Network Access và quyền đọc.")
        return
    raw = make_local(make_frame(snapshot["raw"]))
    processed = make_local(make_frame(snapshot["processed"], processed=True))
    if raw.empty:
        st.info("Không có bản ghi trong khoảng chọn. Mở rộng khoảng thời gian hoặc bật 'Xem lịch sử từ mẫu mới nhất'; kiểm tra Wokwi và collector nếu muốn dữ liệu mới.")
        if snapshot["latest_time"]:
            st.caption("Mẫu cảm biến mới nhất: " + show_time(snapshot["latest_time"]))
        return
    if snapshot["truncated"]:
        st.warning(f"Chỉ tải {MAX_ROWS:,} mẫu mới nhất. Thu hẹp khoảng chọn để tiền xử lý đủ dữ liệu; nút xử lý bị khóa khi cắt bớt mẫu.")
    if snapshot["processed_truncated"] or snapshot["metrics_truncated"]:
        st.warning(f"Kết quả xử lý hoặc phép đo vượt giới hạn {MAX_ROWS:,} dòng; thống kê chỉ dùng các dòng đã tải.")
    ordered = raw.sort_values("received_at", kind="stable") if "received_at" in raw and raw.received_at.notna().all() else raw
    last = ordered.iloc[-1]
    reception = raw.received_at.max() if "received_at" in raw else None
    state, age = freshness(reception, datetime.now(timezone.utc))
    with st.container(border=True):
        a, b = st.columns([3, 2])
        a.markdown("**" + ("Mô phỏng offline" if demo_mode else state) + "** · " + html.escape(selected_device))
        a.caption("Lần nhận cuối: " + show_time(reception) + (f" · cách đây {age:.0f}s" if age is not None and age >= 0 else ""))
        b.caption(("DEMO · cảm biến và phép đo giả lập" if demo_mode else "MongoDB: đọc thành công · MQTT: chưa kiểm tra trực tiếp") +
                  "\n\nTải lúc: " + show_time(datetime.now(timezone.utc)))
    st.caption(f"Khoảng hiển thị: {show_time(snapshot['start'])} → {show_time(snapshot['end'])} · {len(raw):,} mẫu gốc · {len(processed):,} cửa sổ xử lý")
    cols = st.columns(4)
    for col, (key, (label, unit, color)) in zip(cols, SENSOR_INFO.items()):
        previous = ordered.iloc[-2].get(key) if len(ordered) > 1 else None
        current = last.get(key)
        delta = current-previous if current is not None and previous is not None and not pd.isna(current) and not pd.isna(previous) else None
        col.metric(label, fmt(current, unit, 0 if key == "rssi" else 1),
                   delta=fmt(delta, unit) if delta is not None else None, delta_color="off")
    tabs = st.tabs(["Giám sát cảm biến", "Tiền xử lý", "Chất lượng & độ trễ", "Bảng dữ liệu"])
    with tabs[0]:
        st.subheader("Diễn biến cảm biến")
        st.caption("Theo dõi từng kênh trong cùng khoảng thời gian. Di chuột để đọc giá trị; kéo vùng biểu đồ để phóng to.")
        for keys in [("temperature", "humidity"), ("distance_cm", "rssi")]:
            cols = st.columns(2)
            for col, key in zip(cols, keys):
                label, unit, color = SENSOR_INFO[key]
                with col, st.container(border=True):
                    st.markdown(f"**{label}**")
                    chart(line_plot(raw, key, unit, color), "live_"+key)
    context = (selected_device, minutes, window_seconds, historical, demo_mode)
    with tabs[1]:
        preprocessing_tab(snapshot, raw, processed, context, demo_mode)
    with tabs[2]:
        quality_tab(snapshot, raw, processed)
    with tabs[3]:
        table_tab(raw, processed)
    st.caption("IoT Lab 2 · ESP32 / MQTT / MongoDB / Streamlit · Giá trị thiếu hiển thị —, không thay bằng 0.")


st.markdown('<div class="eyebrow">IOT LAB 02 / SENSOR STUDIO</div>', unsafe_allow_html=True)
st.title("Giám sát dữ liệu IoT")
st.markdown('<div class="hero-note">Thu thập, làm sạch và phân tích dữ liệu cảm biến trong một không gian.</div>', unsafe_allow_html=True)
with st.sidebar:
    st.markdown("### Sensor Studio")
    st.caption("ESP32 → MQTT → MongoDB")
    demo_mode = st.checkbox("Dùng dữ liệu demo offline", value=os.getenv("IOT_DEMO", "false").lower() == "true", key="demo_mode")
    if demo_mode:
        st.warning("DEMO · Dữ liệu mô phỏng, không dùng làm kết quả thực nghiệm.")
        devices = ["esp32_demo"]
    else:
        if not MONGO_URI or "<" in MONGO_URI or ">" in MONGO_URI:
            st.info("Điền MONGO_URI vào python/.env để đọc Atlas, hoặc bật demo để khám phá giao diện.")
            st.stop()
        if len({RAW_NAME, PROCESSED_NAME, METRICS_NAME}) != 3:
            st.error("Tên ba collection phải khác nhau. Kiểm tra .env.")
            st.stop()
        try:
            devices = find_devices()
        except Exception as exc:
            st.error("Không kết nối được Atlas: " + type(exc).__name__)
            st.caption("Kiểm tra URI, IP Network Access và quyền database. Thông tin đăng nhập không được hiển thị.")
            st.stop()
        if not devices:
            st.info("Chưa có thiết bị trong collection gốc. Chạy Wokwi và MQTT collector rồi làm mới trang.")
            st.stop()
    st.markdown("---")
    selected_device = st.selectbox("Thiết bị", devices, key="device")
    minutes = st.selectbox("Khoảng thời gian", [5, 15, 30, 60, 180, 360, 720, 1440], index=2,
                           format_func=lambda x: f"{x} phút" if x < 60 else f"{x//60} giờ", key="minutes")
    historical = st.checkbox("Xem lịch sử từ mẫu mới nhất", value=False, disabled=demo_mode, key="historical")
    st.caption("Mặc định tính từ giờ hiện tại. Chế độ lịch sử tính từ timestamp cảm biến mới nhất.")
    window_seconds = st.selectbox("Cửa sổ resampling", [15, 30, 60, 300], index=1,
                                  format_func=lambda x: f"{x} giây", key="window")
    refresh = st.selectbox("Tự làm mới", [0, 10, 30, 60], index=1,
                          format_func=lambda x: "Tắt" if x == 0 else f"Mỗi {x} giây", key="refresh")
    if st.button("Làm mới ngay", key="refresh_now", width="stretch"):
        read_data.clear()
        find_devices.clear()
    st.caption("Làm mới chỉ tải dữ liệu. Tiền xử lý chạy khi bạn bấm nút hoặc chạy script CLI.")


@st.fragment(run_every=f"{refresh}s" if refresh else None)
def live_content():
    run_dashboard()


live_content()
