"""Các hàm xử lý dữ liệu thuần Python cho dashboard Bài thực hành số 2.
Không cần kết nối MongoDB để chạy test các hàm này.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SENSORS = ("temperature", "humidity", "distance_cm")


def make_frame(records, processed=False) -> pd.DataFrame:
    """Chuyển list documents MongoDB thành DataFrame sạch để hiển thị.

    Dữ liệu hiển thị không thay đổi bất cứ record nào trong database.
    """
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame.from_records(records)
    if "time" not in df:
        return pd.DataFrame()
    df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    if df.empty:
        return df
    if "received_at" in df:
        df["received_at"] = pd.to_datetime(df["received_at"], errors="coerce", utc=True)
    if "processed_at" in df:
        df["processed_at"] = pd.to_datetime(df["processed_at"], errors="coerce", utc=True)
    numeric_cols = list(SENSORS) + ["rssi", "sequence", "uptime_s", "latency_ms",
                                   "sample_count", "outlier_count", "window_seconds"]
    for sensor in SENSORS:
        numeric_cols += [sensor + "_rolling_mean_3", sensor + "_delta", sensor + "_norm"]
    for key in numeric_cols:
        if key in df:
            df[key] = pd.to_numeric(df[key], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if processed and "window_seconds" in df:
        df = df.drop_duplicates(subset=["time", "window_seconds"], keep="last")
    return df


def sequence_audit(raw: pd.DataFrame) -> dict:
    """Đếm gap theo sequence trong từng giai đoạn uptime không giảm.

    Gap là nghi ngờ thiếu gói, không thể khẳng định nguyên nhân mạng.
    """
    result = {"suspected_missing": 0, "gaps": 0, "reboot_hints": 0, "duplicates": 0}
    if raw.empty or "sequence" not in raw:
        return result
    relevant = raw.dropna(subset=["sequence"]).copy()
    if relevant.empty:
        return result
    result["duplicates"] = int(relevant.duplicated(subset=["time", "sequence"]).sum())
    relevant = relevant.drop_duplicates(subset=["time", "sequence"])
    # Reception order remains useful when a device clock goes backwards.
    if "received_at" in relevant and relevant["received_at"].notna().all():
        relevant = relevant.sort_values("received_at", kind="stable")
    if "uptime_s" not in relevant:
        return result
    for i in range(1, len(relevant)):
        prev = relevant.iloc[i - 1]
        cur = relevant.iloc[i]
        if pd.isna(prev["uptime_s"]) or pd.isna(cur["uptime_s"]):
            continue
        if cur["uptime_s"] < prev["uptime_s"]:
            result["reboot_hints"] += 1
            continue
        diff = int(cur["sequence"] - prev["sequence"])
        if diff > 1:
            result["gaps"] += 1
            result["suspected_missing"] += diff - 1
    return result


def median_delivery_offset_ms(raw: pd.DataFrame):
    """Trung vị chênh lệch thời điểm nhận với thời điểm thiết bị ghi.

    Chỉ là chênh lệch clock+truyền, không tự động suy ra độ trễ mạng.
    """
    if raw.empty:
        return None
    if "latency_ms" in raw:
        s = pd.to_numeric(raw["latency_ms"], errors="coerce").dropna()
    elif "received_at" in raw:
        s = (raw["received_at"] - raw["time"]).dt.total_seconds() * 1000
        s = s.dropna()
    else:
        return None
    return float(s.median()) if not s.empty else None


def received_period_ms(raw: pd.DataFrame):
    if raw.empty or "received_at" not in raw:
        return None
    sorted_times = raw["received_at"].dropna().sort_values()
    diff = sorted_times.diff().dt.total_seconds().mul(1000).dropna()
    diff = diff[diff > 0]
    return float(diff.median()) if not diff.empty else None


def make_local(df: pd.DataFrame, timezone_name: str = "Asia/Ho_Chi_Minh") -> pd.DataFrame:
    if df.empty:
        return df.copy()
    result = df.copy()
    result["time_local"] = result["time"].dt.tz_convert(timezone_name)
    if "received_at" in result:
        result["received_local"] = result["received_at"].dt.tz_convert(timezone_name)
    return result


def numeric_summary(values, nonnegative=True):
    """Mean/min/max/P95 use finite measurements only; missing is never zero."""
    series = pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce")
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    if nonnegative:
        series = series[series >= 0]
    if series.empty:
        return dict(count=0, mean=None, min=None, max=None, p95=None, median=None)
    return dict(count=len(series), mean=float(series.mean()), min=float(series.min()),
                max=float(series.max()), p95=float(series.quantile(.95)),
                median=float(series.median()))


def quality_table(raw):
    """Stored-record quality only; rejected MQTT messages are not observable here."""
    from preprocess_iot import LIMITS
    rows = []
    for key in (*SENSORS, "rssi"):
        values = pd.to_numeric(raw.get(key, pd.Series(index=raw.index, dtype=float)),
                               errors="coerce").replace([np.inf, -np.inf], np.nan)
        lo, hi = LIMITS[key]
        missing = int(values.isna().sum())
        invalid = int((values.notna() & ~values.between(lo, hi)).sum())
        rows.append({"Cảm biến": key, "Thiếu": missing, "Ngoài miền": invalid,
                     "Hợp lệ (%)": (100 * (len(raw)-missing-invalid) / len(raw)) if len(raw) else None})
    return pd.DataFrame(rows)


def freshness(received_at, now, threshold_seconds=30):
    """Device freshness is inferred from server reception, never sensor clocks."""
    if received_at is None or pd.isna(received_at):
        return "Không xác định", None
    age = (pd.Timestamp(now) - pd.Timestamp(received_at)).total_seconds()
    if age < -5:
        return "Kiểm tra đồng hồ máy", age
    return ("Đang nhận dữ liệu" if age <= threshold_seconds else "Dữ liệu đã cũ"), max(0, age)
