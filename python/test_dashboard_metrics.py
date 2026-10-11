import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from datetime import datetime, timedelta, timezone
from dashboard_metrics import make_frame, sequence_audit, median_delivery_offset_ms, received_period_ms, make_local

start = datetime(2026, 10, 11, tzinfo=timezone.utc)
rows = []
for seq, sec, uptime in [(1, 0, 5), (2, 5, 10), (4, 10, 15), (1, 15, 2), (2, 20, 7)]:
    t = start + timedelta(seconds=sec)
    rows.append(dict(time=t, received_at=t + timedelta(seconds=70), sequence=seq,
                     uptime_s=uptime, temperature=25, humidity=50, distance_cm=200,
                     latency_ms=70000))
raw = make_frame(rows)
a = sequence_audit(raw)
assert a['suspected_missing'] == 1, a
assert a['reboot_hints'] == 1, a
assert median_delivery_offset_ms(raw) == 70000
assert received_period_ms(raw) == 5000
assert make_local(raw).iloc[0]['time_local'].hour == 7
processed = make_frame([{'time':start, 'window_seconds':30, 'temperature':25,
                         'temperature_norm':0.2}], processed=True)
assert len(processed) == 1
assert processed['temperature_norm'].iloc[0] == 0.2
print('[OK] Đã kiểm tra các hàm tính chỉ số Dashboard bằng dữ liệu giả lập.')
