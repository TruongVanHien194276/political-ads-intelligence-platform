# Chạy project từ đầu đến cuối

Đường chạy chính là local trên Mac M3 8 GB, không cần bật Docker, Airflow hay cloud để xem web

## 1 — Clone và smoke test không cần tài khoản

```bash
git clone https://github.com/TruongVanHien194276/political-ads-intelligence-platform.git
cd political-ads-intelligence-platform
python3.14 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev,api]'
cd apps/web
npm ci
npx playwright install chromium
cd ../..
make smoke
terraform -chdir=infra/terraform init -backend=false -input=false
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform validate
```

- Cần Python 3.14, Node 24, npm, Git; Terraform chỉ cần cho bước IaC
- `make smoke` chạy pytest, Ruff, secret scan high-signal, React lint/build và Playwright fixture
- Không cần `GCP_PROJECT_ID`, ADC, raw Parquet, API thật hay secret ở bước này
- Trên Mac 8 GB, chỉ mở một phần nặng cùng lúc; Playwright dùng một Chromium worker
- Python test sẽ có một deprecation warning từ FastAPI TestClient, không phải test fail

## 2 — Lấy raw snapshot, chỉ khi muốn dùng dữ liệu thật

Repo không chứa raw data hoặc warehouse, nên clone mới không thể tự mở dashboard dữ liệu thật ngay

```bash
gcloud auth application-default login
export GCP_PROJECT_ID=political-ads-intel-194276
export BIGQUERY_LOCATION=US
export MAX_QUERY_MIB=256
political-ads-raw --week-start 2026-10-11
```

- Lệnh trên chỉ dry-run BigQuery và in estimate bytes, chưa tải row
- Xác nhận đúng project, quota và chế độ billing trước khi thêm `--execute`; query công khai vẫn có thể dùng quota hoặc phát sinh phí nếu project bật billing
- `MAX_QUERY_MIB` là chặn bytes cho mỗi job, không phải ngân sách tháng
- Nếu không muốn truy cập BigQuery, dừng ở bước 1; smoke test vẫn chạy được

Sau khi chủ động chấp nhận query nguồn, lấy 20 tuần lịch sử bằng năm cửa sổ, tối đa bốn tuần mỗi lệnh

```bash
political-ads-raw --backfill-start 2026-05-31 --backfill-end 2026-06-21 --execute
political-ads-raw --backfill-start 2026-06-28 --backfill-end 2026-07-19 --execute
political-ads-raw --backfill-start 2026-07-26 --backfill-end 2026-08-16 --execute
political-ads-raw --backfill-start 2026-08-23 --backfill-end 2026-09-13 --execute
political-ads-raw --backfill-start 2026-09-20 --backfill-end 2026-10-11 --execute
```

Mỗi snapshot nằm ở `data/raw/source=.../table=.../week_start=.../run_id=.../` với `data.parquet` và `manifest.json`; cùng phiên bản nguồn trả `SKIPPED`, nguồn sửa sẽ có run ID mới, không ghi đè âm thầm

## 3 — Bronze, Silver, Gold và quality gate

dbt-duckdb dùng Python 3.13 riêng vì môi trường pipeline là Python 3.14

```bash
python3.13 -m venv data/tmp/week4/venv
data/tmp/week4/venv/bin/python -m pip install 'duckdb==1.5.6' 'dbt-core>=1.11,<1.12' 'dbt-duckdb==1.11.0'
make week4
make week6
```

- `make week4` tạo warehouse lần đầu; `make week6` build candidate rồi chỉ publish nếu dbt và quality checks PASS
- Local Gold ở `data/tmp/week4/warehouse.duckdb`; backup và audit ở `data/tmp/week6/`
- Nếu raw đã có phiên bản mới, chạy lại `make week6`, không sửa file DuckDB thủ công
- Kiểm tra dbt độc lập: `WEEK4_DB_PATH="$PWD/data/tmp/week4/warehouse.duckdb" data/tmp/week4/venv/bin/dbt test --project-dir transform --profiles-dir transform`
- Đối soát tối thiểu: `raw = valid + quarantine`, Silver current không trùng advertiser × tuần, Gold fact spend = BI/API mart spend

## 4 — Business và BI snapshot

```bash
make week9
make week10
```

- `make week9` tạo SQL đối soát, ảnh trend/top share/HHI/distribution/movers và evidence trong `reports/week9/`
- `make week10` tạo `data/exports/powerbi/political_ads_gold_<last_week>.xlsx` và manifest, đối soát 6 KPI Excel ↔ SQL
- Đây là snapshot local, không phải report Power BI Cloud đã publish
- Nếu muốn import Power BI Cloud, dùng đúng tài khoản được chủ project chọn, import workbook, tạo quan hệ `DimAdvertiser → FactSpend` và `DimWeek → FactSpend` theo khóa 1:n, rồi so ít nhất 3 KPI cùng filter tuần với `reports/week10/evidence.json`
- Không vẽ geography/targeting share khi mart còn ghi `unavailable`; không tính CTR/ROAS vì nguồn không có click, conversion hay revenue
- Workbook nằm trong `.gitignore`; sau mỗi lần Gold đổi phải export lại và kiểm checksum trước khi import

## 5 — ML, registry và forecast

```bash
python3.13 -m venv data/tmp/week11/venv
data/tmp/week11/venv/bin/python -m pip install --only-binary=:all: -r requirements/week11.txt
make week11
```

Trên Mac, LightGBM cần `libomp`; nếu import báo thiếu `libomp.dylib`, chạy `brew install libomp` rồi thử lại

```bash
python3.13 -m venv data/tmp/week12/venv
data/tmp/week12/venv/bin/python -m pip install --only-binary=:all: -r requirements/week12.txt
make week12
make week13
```

- `make week12` tải Chronos weights lần đầu, cần Internet và nhiều dung lượng; tắt Airflow/browser khác khi train trên máy 8 GB
- Week 11/12 dùng cùng rolling folds, không điền 0 cho tuần thiếu; naive đang là champion do challenger không qua ngưỡng promotion
- `make week13` cần Gold DQ PASS, ghi `data/tmp/week13/forecast.duckdb`, MLflow registry local và evidence/drift
- Actual tuần 2026-10-18 chưa có trong snapshot hiện tại, nên forecast live hiện là `pending`, không tính lỗi dự báo giả

## 6 — API và web

Terminal A tại project root

```bash
.venv/bin/python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Terminal B tại `apps/web`

```bash
cd apps/web
npm run dev
```

Mở [web local](http://127.0.0.1:5173) và [OpenAPI](http://127.0.0.1:8000/docs)

```bash
make week14
cd apps/web && npm run test:live && cd ../..
.venv/bin/python scripts/week16_audit.py
```

- Chạy `make week14` sau lần publish/score mới để làm mới hash Gold và đối soát SQL–API
- `test:live` cần API cổng 8000, tự chạy Vite nếu chưa có và cập nhật ảnh + evidence web
- `week16_audit.py` kiểm hash Gold ở BI snapshot/forecast/API/web, đo 50 request localhost p50/p95 và tổng bytes từ manifest lịch sử
- Nếu port 8000 hoặc 5173 bận, dừng process cũ đúng cổng; đừng xóa dữ liệu để chữa lỗi port

## 7 — Airflow và cloud là nhánh tùy chọn

- Airflow local điều phối `extract → load → transform → quality → publish` trên raw snapshot đã có, không cần gọi BigQuery
- Cài Airflow 3.3.2 trong Python 3.12 riêng, dùng constraints cùng phiên bản

```bash
python3.12 -m venv data/tmp/week7/airflow-venv
data/tmp/week7/airflow-venv/bin/python -m pip install 'apache-airflow==3.3.2' --constraint 'https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt'
```

- Trong cả hai terminal ở project root chạy `. scripts/week7_env.sh` để giữ cùng `AIRFLOW_HOME` và API port 8082
- Terminal A chạy `airflow standalone`, Terminal B chạy `airflow dags unpause political_ads_weekly` rồi `airflow dags trigger --logical-date 2026-10-12T00:00:00+00:00 political_ads_weekly`
- Airflow logical date thứ Hai UTC tương ứng raw week Chủ nhật; retry cùng candidate, replay đã PASS là no-op
- Cloud Run/Workflows/Scheduler mới có code, SQL, Terraform và mock tests; project chưa bật billing nên không có cloud execution, log hoặc cost thật
- Terraform Scheduler mặc định paused; không chạy `terraform apply` hoặc bật lịch nếu chưa có ngân sách và quyền được duyệt

## Khi hỏng ở đâu thì xem ở đó

| Hiện tượng | Kiểm tra |
| --- | --- |
| `GCP_PROJECT_ID` hoặc ADC thiếu | Bước 2 chỉ cần cho query nguồn; bước 1 không cần |
| `dbt` báo thiếu profile/project | Dùng `dbt build --project-dir transform --profiles-dir transform`, flag đứng sau `build` |
| Publish fail | Xem audit `audit.pipeline_runs`, `audit.dq_scorecard`, quarantine và dbt log; Gold cũ vẫn được giữ |
| Forecast stale | So hash Gold, DQ PASS, cutoff, registry version rồi chạy lại `make week13` |
| API 503 | Xem file Gold/forecast, `/api/v1/pipeline-health`; không đổi missing thành 0 |
| Web trống hoặc lỗi | Kiểm tra API cổng 8000, Vite cổng 5173 và request `/api` trong Network tab |
| Power BI lệch SQL | Kiểm cùng filter tuần, đơn vị USD, workbook hash và ngày snapshot, export lại nếu Gold đổi |

Số liệu và sơ đồ hiện tại nằm ở [reports/EVIDENCE.md](reports/EVIDENCE.md); `reports/week*/evidence.json` và ảnh là bản kiểm chứng cụ thể, không thay thế dữ liệu raw trong `.gitignore`
