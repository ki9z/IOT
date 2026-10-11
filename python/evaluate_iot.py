"""Read-only summary of IoT Lab 2 Atlas data for the final report.

Run from the folder with .env:
  python evaluate_iot.py

Creates metrics_report.txt without URI/credentials. Timestamp deltas are not
end-to-end MQTT/storage latency unless clocks are aligned and write-end recorded.
"""
import os
import statistics
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError
from latency_utils import summarize_durations
from dashboard_metrics import make_frame, sequence_audit, quality_table, numeric_summary

load_dotenv(Path(__file__).with_name('.env'))
URI = os.getenv('MONGO_URI','').strip()
DB = os.getenv('MONGO_DB','iot_lab2')
RAW = os.getenv('MONGO_COLLECTION','iot_raw')
PROC = os.getenv('MONGO_PROCESSED_COLLECTION','iot_processed')
METRICS = os.getenv('MONGO_WRITE_METRICS_COLLECTION','iot_write_metrics')
MAX = 10000


def utc(dt):
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def median_str(values, suffix=''):
    if not values: return 'N/A'
    return f'{statistics.median(values):.1f}{suffix}'


def inspect_one(rows, device):
    frame = make_frame(rows)
    audit = sequence_audit(frame)
    rows.sort(key=lambda x: utc(x['time']) if utc(x.get('time')) else datetime.min.replace(tzinfo=timezone.utc))
    seen=set(); prev=None
    periods=[]; receive_periods=[]; offset=[]
    for d in rows:
        t=utc(d.get('time'))
        if t is None: continue
        seq=d.get('sequence'); uptime=d.get('uptime_s'); rec=utc(d.get('received_at'))
        key=(seq,t)
        if key in seen:
            continue
        seen.add(key)
        if rec: offset.append((rec-t).total_seconds()*1000)
        if prev:
            pt, ps, pu, prec = prev
            dt=(t-pt).total_seconds()*1000
            if dt>0: periods.append(dt)
            if rec and prec and rec>prec: receive_periods.append((rec-prec).total_seconds()*1000)
        prev=(t,seq,uptime,rec)
    return [
        f'  Số bản ghi xét: {len(rows)}',
        f'  Bản ghi lặp (sequence, time) nghi vấn: {audit["duplicates"]}',
        f'  Số sequence nghi thiếu: {audit["suspected_missing"]}',
        f'  Dấu hiệu reset thiết bị: {audit["reboot_hints"]}',
        f'  Trung vị chu kỳ time cảm biến: {median_str(periods," ms")}',
        f'  Trung vị chu kỳ Python nhận: {median_str(receive_periods," ms")}',
        f'  Trung vị received_at - time: {median_str(offset," ms")}',
    ] + ['  Chất lượng cảm biến trong các bản ghi đã lưu:'] + [
        f'    {r["Cảm biến"]}: thiếu={r["Thiếu"]}, ngoài miền={r["Ngoài miền"]}, hợp lệ={r["Hợp lệ (%)"]:.1f}%'
        for r in quality_table(frame).to_dict('records') if r['Hợp lệ (%)'] is not None
    ]


def main():
    if not URI or '<' in URI or '>' in URI:
        raise SystemExit('Chưa có MONGO_URI trong .env')
    with MongoClient(URI,tlsCAFile=certifi.where(),serverSelectionTimeoutMS=12000) as cli:
        cli.admin.command('ping')
        db=cli[DB]
        infos={x['name']:x for x in db.list_collections() if x['name'] in (RAW,PROC,METRICS)}
        raw=db[RAW]
        count=raw.count_documents({})
        proc_count=db[PROC].count_documents({}) if PROC in infos else 0
        lines=[
            'BÁO CÁO KIỂM TRA DỮ LIỆU IoT LAB 2',
            'Thời điểm kiểm tra (UTC): '+datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'Database: '+DB, 'Raw collection: '+RAW,
            'Tổng bản ghi gốc tại lúc kiểm tra: '+str(count),
            'Processed collection: '+PROC,
            'Tổng cửa sổ đã lưu tại lúc kiểm tra: '+str(proc_count),
        ]
        opt=infos.get(RAW,{}).get('options',{})
        ts=opt.get('timeseries',{})
        lines+=['Dạng Time-Series: '+('YES' if ts else 'NO'),
                'timeField: '+str(ts.get('timeField','N/A')),
                'metaField: '+str(ts.get('metaField','N/A')),
                'Retention expireAfterSeconds: '+str(opt.get('expireAfterSeconds','NOT SET'))]
        docs=list(raw.find({}, {'time':1,'received_at':1,'sequence':1,'uptime_s':1,
                                'metadata.device_id':1,'temperature':1,'humidity':1,
                                'distance_cm':1,'rssi':1}).sort('time',-1).limit(MAX))
        if count>MAX:lines.append(f'CẢNH BÁO: Chỉ thống kê {MAX} bản ghi mới nhất / {count} bản ghi.')
        bydev=defaultdict(list)
        for d in docs:bydev[str(d.get('metadata',{}).get('device_id','unknown'))].append(d)
        for device, rows in sorted(bydev.items()):
            lines+=['','Device: '+device]+inspect_one(rows, device)
        if METRICS in infos:
            metric_total = db[METRICS].count_documents({})
            measured = list(db[METRICS].find(
                {}, {'_id': 0, 'mongodb_insert_ms': 1, 'collector_to_db_ms': 1}
            ).sort('stored_at', -1).limit(MAX))
            perf = summarize_durations(measured)
            lines += [
                '',
                'HIỆU NĂNG GHI (collector V2 có đo đồng hồ đơn điệu):',
                'Collection: ' + METRICS,
                'Tổng phép đo: ' + str(metric_total),
                'Số phép đo đã phân tích: ' + str(perf['measurements']),
                'Trung vị MongoDB insert_one() (ms): ' +
                    median_str([r['mongodb_insert_ms'] for r in measured
                                if isinstance(r.get('mongodb_insert_ms'), (int, float))]),
                'Trung vị callback MQTT → MongoDB xác nhận (ms): ' +
                    median_str([r['collector_to_db_ms'] for r in measured
                                if isinstance(r.get('collector_to_db_ms'), (int, float))]),
            ]
            if metric_total > MAX:
                lines.append(f'Chú ý: thống kê độ trễ dựa trên {MAX} phép đo mới nhất.')
            for field in ('mongodb_insert_ms', 'collector_to_db_ms'):
                summary = numeric_summary([r.get(field) for r in measured])
                lines.append(f'  {field}: mẫu hợp lệ={summary["count"]}, '
                             f'mean={summary["mean"]}, P95={summary["p95"]}, '
                             f'min={summary["min"]}, max={summary["max"]} ms')
        else:
            lines += ['', 'HIỆU NĂNG GHI: Chưa có ' + METRICS +
                      '. Hãy chạy collector V2 phiên bản mới để ghi phép đo.']

        lines+=['','GIỚI HẠN ĐÁNH GIÁ:',
                '- received_at - time chứa sai lệch đồng hồ; KHÔNG phải đo độ trễ mạng MQTT hoặc end-to-end chính xác.',
                '- Thời gian MongoDB insert_one và callback→DB là kết quả đo monotonic của Python, không bao gồm MQTT/Wokwi.',
                '- Để đo end-to-end (thiết bị→DB) chính xác cần đồng bộ và kiểm chứng NTP/đồng hồ thiết bị và máy Python.',
                '- Sequence thiếu có thể do tắt collector, publish lỗi, restart, hoặc rơi tin QoS 0.',
                '- Thống kê dựa trên bản ghi đã lưu (chưa đo các tin chưa được tiếp nhận).']
        payload='\n'.join(lines)+'\n'
        Path('metrics_report.txt').write_text(payload,encoding='utf-8')
        print(payload)
        print('[OK] Đã tạo metrics_report.txt (không chứa URI/mật khẩu)')


if __name__=='__main__':
    try: main()
    except (PyMongoError,OSError) as e:
        print('[ERROR] Kết nối/đọc MongoDB thất bại:',type(e).__name__)
        raise SystemExit(1)
