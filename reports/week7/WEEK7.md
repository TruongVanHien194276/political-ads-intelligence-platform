# Tuần 7 — Airflow local

- Python 3.12 và Airflow 3.3.2 nằm trong `data/tmp/week7/airflow-venv`
- Chạy một `LocalExecutor` task tại một thời điểm để phù hợp Mac M3 8 GB RAM
- Luồng: `extract → load → transform → quality → publish`
- `extract` chọn snapshot Parquet local đã có và kiểm tra manifest, không gọi BigQuery hoặc tạo chi phí cloud
- `load` tạo candidate DuckDB; `publish` chỉ thay warehouse sau khi dbt và quality gate đạt
- `logical date` là thứ Hai UTC, tương ứng `week_start` Chủ nhật hôm trước
- Run cùng logical date dùng cùng source run ID sẽ có cùng publish key; publish lần hai là no-op
- Nếu Google sửa nguồn và xuất hiện snapshot mới, source run ID và publish key mới cho phép cập nhật có lịch sử

## Chạy và xem Graph/Grid

Tại project root, Terminal A:

```bash
. scripts/week7_env.sh
command -v airflow  # phải nằm trong data/tmp/week7/airflow-venv/bin
airflow standalone
```

Terminal B, cũng tại project root:

```bash
. scripts/week7_env.sh
airflow dags list
airflow dags unpause political_ads_weekly
airflow dags trigger --logical-date 2026-10-12T00:00:00+00:00 political_ads_weekly
airflow dags list-runs political_ads_weekly -o plain
airflow tasks states-for-dag-run political_ads_weekly 2026-10-12T00:00:00+00:00 -o plain
```

- UI: [Airflow local](http://127.0.0.1:8082) → `political_ads_weekly` → Graph hoặc Grid → chọn run
- Mật khẩu admin được Airflow ghi vào `data/tmp/week7/airflow-home/simple_auth_manager_passwords.json.generated`; không commit hoặc chụp màn hình file này
- Trước khi trigger kỳ khác, cần có raw snapshot cho Chủ nhật tương ứng trong `data/raw/`
- Airflow không cho tạo run thứ hai có cùng logical date; để replay, dùng Clear trên run đó trong UI hoặc lệnh dưới

```bash
airflow tasks clear political_ads_weekly \
  --start-date 2026-10-12 --end-date 2026-10-12 --yes
```

## Backfill và thử retry

Chạy từng kỳ để không vượt một active run; dữ liệu local hiện có cho các logical date `2026-09-21`, `2026-09-28`, `2026-10-05`, `2026-10-12`

```bash
airflow dags trigger --logical-date 2026-09-28T00:00:00+00:00 political_ads_weekly
airflow dags trigger --logical-date 2026-10-05T00:00:00+00:00 \
  --conf '{"fail_once":"transform"}' political_ads_weekly
```

- Run thứ hai cố ý lỗi một lần ở `transform`, sau 30 giây retry cùng candidate rồi tiếp tục
- Đối chiếu `audit.pipeline_runs`, `audit.dq_scorecard` và số dòng Bronze/Silver/Gold trong `data/tmp/week4/warehouse.duckdb`
- Clear một run đã thành công để replay; các task sau `extract` in `ALREADY_PUBLISHED`, không tạo duplicate hay backup mới

## Khi có lỗi

| Triệu chứng | Nguyên nhân và cách xử lý |
| --- | --- |
| `no such table: dag` | Terminal B chưa dùng cùng `AIRFLOW_HOME`; source `scripts/week7_env.sh` trong cả hai Terminal |
| `Invalid auth token` | API chạy ở `8082` nhưng Execution API trỏ `8080`; source env script rồi khởi động lại `airflow standalone` |
| `UNIQUE constraint failed: dag_run...logical_date` | Run cho ngày đó đã tồn tại; dùng Clear thay vì trigger lại |
| `Chưa có raw snapshot` | Chọn logical date phù hợp raw đã tải hoặc nạp snapshot bằng CLI Tuần 3 trước; không tự query BigQuery |
| `Quality gate fail` | Xem task log, `audit.dq_scorecard` và quarantine; sửa nguồn/candidate rồi Clear, không bỏ qua quality gate |
| `ModuleNotFoundError` hoặc sai Python | Airflow dùng venv Python 3.12, task dữ liệu dùng `.venv/bin/python` của project; kiểm tra cả hai môi trường |

## Bằng chứng run

- Ba logical date `2026-09-28`, `2026-10-05`, `2026-10-12` đều `success` trong Airflow; mỗi run có 5 task và 16 quality checks PASS
- Publish key lần lượt `2a910a9f164a`, `9ddbc37aad90`, `05555a70f85b`; audit lưu `PASS`
- Run `2026-10-05` có `transform` attempt 1 lỗi mô phỏng, attempt 2 thành công rồi publish
- Clear và chạy lại `2026-10-12` trả `ALREADY_PUBLISHED` ở 4 task sau extract; SHA-256 của warehouse giữ nguyên `c1a73d82801fbf856a58a07e56e0ec6a6856d7d2da002ee33b388984247a06b4`
- Bronze, Silver history, Silver current và Gold fact đều 9.699 dòng; duplicate Silver current key bằng 0
- Run tự động `2026-10-19` thất bại trong lúc cấu hình sai cổng, không phải bằng chứng backfill; chưa có raw snapshot `2026-10-18`
