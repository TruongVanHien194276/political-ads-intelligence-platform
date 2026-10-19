# Tuần 6 — Data quality và backfill

## Luồng publish

- Chạy `make week6` từ project root
- Publisher sao chép warehouse hiện hành sang candidate cùng filesystem
- Kiểm tra schema Parquet bằng metadata, nạp Bronze, build dbt Silver, promote current, build dbt Gold
- Chỉ thay warehouse đang dùng sau khi dbt tests và toàn bộ blocking checks đều pass
- Trước khi thay, lưu bản cũ trong `data/tmp/week6/backups/`; dữ liệu và backup local không commit
- Nếu lỗi, candidate bị bỏ, audit ghi `FAIL`, Silver/Gold đang dùng giữ nguyên
- Từ Tuần 6, dùng `make week6` cho luồng publish; `make week5` chỉ còn phù hợp khi phát triển/thử nghiệm trực tiếp

## Scorecard

Schema nguồn được kiểm tra trước khi chạy pipeline; nếu lệch, publisher ghi audit `FAIL`. `audit.dq_scorecard` lưu từng check sau build theo `run_id`: đối soát raw/valid/quarantine, số dòng latest snapshot và Silver, duplicate current key, spend âm, phiên bản nguồn mơ hồ, số dòng Silver–fact, tổng spend qua các Gold marts và bảo toàn raw/history cũ

`latest_week_lag_days` là cảnh báo nếu tuần nguồn cuối cùng cách ngày chạy quá 14 ngày; backfill lịch sử không bị chặn chỉ vì freshness

`audit.pipeline_runs` lưu thời điểm, trạng thái `PASS`/`FAIL` và lý do. Khi lỗi, message kèm thống kê `validation_error` của quarantine nếu có

## Bằng chứng chạy lại trên bốn snapshot local

| Lần chạy | Audit run ID | Kết quả |
| --- | --- | --- |
| Publish đầu | `28dba10af522` | PASS |
| Publish lại cùng raw | `7b3233ef11a5` | PASS |

- Bốn raw snapshot: 2026-09-20, 2026-09-27, 2026-10-04, 2026-10-11
- Cả hai lần loader trả `SKIPPED` cho cả bốn snapshot; không gọi BigQuery hay tạo raw mới
- Bronze 9.699; Silver history 9.699; Silver current 9.699; Gold fact 9.699
- Tổng Gold fact 268.708.700 USD sau cả hai lần; quarantine hiện tại 0
- Scorecard của hai lần trên có 16/16 checks `PASS` mỗi lần
- dbt Silver 9/9 bước pass, dbt Gold 32/32 bước pass; `dbt test` toàn project chạy xanh
- Source correction và dữ liệu lỗi được kiểm chứng bằng fixture offline trong pytest, không sửa raw snapshot thật

## Test offline

- `tests/test_week4_promote.py`: replay, run sửa/xóa/thêm advertiser, history giữ cả hai run và quarantine chặn promote
- `tests/test_week6_publish.py`: phát hiện schema drift từ Parquet, phân loại reason trong staging SQL, blocking failure không thay warehouse, publish thành công có backup
- Chạy `PATH="$PWD/.venv/bin:$PATH" make check`
- Chạy `WEEK4_DB_PATH="$PWD/data/tmp/week4/warehouse.duckdb" data/tmp/week4/venv/bin/dbt test --project-dir transform --profiles-dir transform`

## Xem audit trên máy

```bash
data/tmp/week4/venv/bin/python - <<'PY'
import duckdb

con = duckdb.connect('data/tmp/week4/warehouse.duckdb', read_only=True)
print(con.execute('''
    SELECT run_id, status, finished_at_utc, message
    FROM audit.pipeline_runs
    ORDER BY finished_at_utc DESC LIMIT 5
''').fetchall())
print(con.execute('''
    SELECT check_name, status, observed, expected
    FROM audit.dq_scorecard
    WHERE run_id = (
        SELECT run_id FROM audit.pipeline_runs
        ORDER BY finished_at_utc DESC LIMIT 1
    )
    ORDER BY check_name
''').fetchall())
con.close()
PY
```

Dùng Python của môi trường dbt ở đây vì môi trường Python 3.14 project chưa có `pytz` để chuyển cột `TIMESTAMPTZ` sang Python datetime

## Giới hạn

- Backfill thực tế ở đây là replay bốn raw snapshot local đã có, chưa tải bản sửa mới từ BigQuery
- Test source correction dùng dữ liệu tổng hợp offline; không khẳng định Google đã sửa bốn snapshot hiện có
- Audit và backup nằm trong `data/tmp/` nên không được đẩy lên Git; báo cáo này giữ kết quả kiểm chứng có thể đọc trong portfolio
