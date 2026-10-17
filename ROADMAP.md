# Political Ads Intelligence Platform — roadmap 16 tuần

## Mục tiêu

- Xây một sản phẩm có dữ liệu thật, pipeline chạy lại được, dashboard business, mô hình dự báo và web app để demo
- Nguồn chính: Google Political Ads public dataset trên BigQuery, lấy qua API chứ không crawl giao diện
- Phạm vi đầu tiên: spend USD theo advertiser-tuần; phân tích Mỹ qua bảng geo riêng vì weekly source không có country
- Người dùng: analyst, nhóm nghiên cứu thị trường hoặc người theo dõi tính minh bạch của quảng cáo chính trị
- Câu hỏi chính: ai đang tăng chi tiêu, thị trường tập trung ra sao, khu vực nào được nhắm mục tiêu, biến động nào bất thường, tuần tới có thể chi bao nhiêu
- Không tính CTR, conversion hay ROAS vì nguồn không có click, đơn hàng hoặc doanh thu
- Đây là kế hoạch, chưa phải các thành phần đã được xây xong

## Stack chính

| Phần | Công nghệ | Dùng để làm gì |
| --- | --- | --- |
| Data ingestion | Python, BigQuery client, PyArrow | Lấy dữ liệu, ghi Parquet, lưu manifest và chạy lại an toàn |
| Data lake | Google Cloud Storage, Parquet | Giữ raw snapshot có thể đối soát |
| Warehouse | BigQuery, SQL, dbt Core | Bronze, Silver, Gold, data model, test và lineage |
| Orchestration | Airflow local, Cloud Run Jobs, Workflows | Chạy batch, retry, backfill và lịch cloud |
| Data quality | dbt tests, SQL assertions, pytest | Chặn dữ liệu lỗi trước khi publish |
| BI và business | Power BI Cloud, DAX, SQL, Plotly | Dashboard, thống kê và insight có số liệu |
| ML | naive baseline, LightGBM, TFT nhỏ, Chronos-2-small, MLflow | So sánh mô hình và dự báo spend theo tuần |
| API | FastAPI, Pydantic | Cấp dữ liệu Gold, forecast và pipeline health cho web |
| Web | React, TypeScript, Vite, TanStack Query, Apache ECharts | Platform 5 màn hình, filter và drill-down |
| DevOps | Docker, GitHub Actions, Terraform, IAM | Test, deploy, phân quyền và theo dõi chi phí |

- Node.js chỉ dùng để chạy và build React, không làm thêm backend Express
- Power BI dành cho phân tích BI, React là giao diện sản phẩm, cả hai dùng cùng metric từ Gold
- Không thêm Kafka, Kubernetes hay Spark vào luồng chính chỉ để có tên công nghệ
- Có thể làm thêm benchmark PySpark `local[2]` trên một mẫu Parquet nhỏ nếu còn thời gian

## Luồng dữ liệu

```text
Google Political Ads BigQuery
  → Python extractor
  → GCS raw Parquet + manifest
  → BigQuery Bronze → dbt Silver → dbt Gold
  → Power BI Cloud
  → ML training → batch forecast → BigQuery forecast mart
  → FastAPI → React web platform

Airflow local hoặc Cloud Scheduler → Workflows → Cloud Run Jobs điều phối batch
GitHub Actions chạy test, Terraform quản lý tài nguyên cloud
```

## Giới hạn để chạy được trên máy

- Máy hiện tại: MacBook Pro M3, 8 GB RAM, khoảng 150 GB trống khi lập kế hoạch
- BigQuery xử lý dữ liệu lớn, Mac chỉ chạy code, test, Airflow một worker hoặc thử mô hình nhỏ
- React và FastAPI chạy trên host khi phát triển, không cần bật Docker cho mọi thứ
- Không chạy đồng thời Airflow, Spark, training và frontend
- Chỉ train trên cohort khoảng 50–200 advertiser có đủ lịch sử, batch nhỏ và early stopping
- Mọi query thử phải dry-run và đặt `maximum_bytes_billed`, `LIMIT` không tự bảo đảm giảm chi phí
- Chưa bật cloud hoặc tải model trong giai đoạn lập roadmap, tài nguyên tính phí chỉ tạo sau khi có project và ngân sách rõ

## Quy tắc dữ liệu trước khi bắt đầu

- Tuần 1 xác minh tên bảng, cột, kiểu, grain và mức cập nhật thực tế của source
- Creative raw có khóa `ad_id` + `snapshot_at`, không cộng spend lifetime qua nhiều snapshot
- Weekly spend hiện có grain ứng viên advertiser + tuần; currency là các cột riêng, không có region
- Spend hoặc impressions dạng khoảng phải giữ `min` và `max`, midpoint nếu dùng phải gắn nhãn ước lượng
- Tuần thiếu dữ liệu không tự đổi thành spend bằng 0
- Forecast có khóa advertiser + origin week + target week + horizon + model version
- Nếu weekly spend không đủ chất lượng để dự báo, chuyển target sang số creative mới theo tuần và ghi rõ lý do

## Kế hoạch từng tuần

### Tuần 1 — Chốt bài toán và kiểm tra nguồn

- Input: Google Political Ads public BigQuery dataset
- Công nghệ: BigQuery SQL, `INFORMATION_SCHEMA`, Python client
- Việc làm: kiểm tra schema, key, ngày cập nhật, null, duplicate, tiền tệ, spend band và độ dài chuỗi tuần
- Output: source inventory, data dictionary v1, profiling SQL, 3–5 câu hỏi business, quyết định target ML
- Xong khi: query nguồn chạy được, biết query tốn bao nhiêu bytes, không hứa KPI mà nguồn không có

### Tuần 2 — Dựng repo và môi trường

- Input: schema và phạm vi từ tuần 1
- Công nghệ: Python, pytest, Ruff, Docker, GitHub Actions, Terraform
- Việc làm: tạo package, CLI dry-run, config từ env, test ingestion bằng mock, CI và IaC plan
- Output: repo chạy `make check` không cần secret, cấu trúc thư mục, cost guardrails
- Xong khi: clone mới vẫn chạy test, key và dữ liệu raw không bị commit

### Tuần 3 — Ingestion và raw lake

- Input: query nguồn đã chốt
- Công nghệ: Python BigQuery client, GCS, Parquet, PyArrow
- Việc làm: lấy một cửa sổ lịch sử giới hạn, tạo raw partition và manifest chứa run ID, query hash, row count, checksum
- Output: extractor CLI hỗ trợ ngày chạy và backfill, raw snapshot trên GCS
- Xong khi: chạy lại cùng kỳ không ghi đè âm thầm hoặc nhân đôi dữ liệu

### Tuần 4 — Bronze và Silver

- Input: raw Parquet và manifest
- Công nghệ: BigQuery load job, SQL `MERGE`, dbt staging
- Việc làm: Bronze append-only, Silver ép kiểu, chuẩn hóa ngày và currency, tách bản lỗi, xử lý source sửa lại
- Output: Bronze, Silver current/history, quarantine và query đối soát
- Xong khi: `raw = valid + quarantine` theo quy tắc đã ghi, replay không tạo duplicate current key

### Tuần 5 — Warehouse và dbt Gold

- Input: Silver
- Công nghệ: BigQuery SQL, dbt Core, dimensional modeling
- Việc làm: tạo dimension, fact theo tuần, marts cho trend, advertiser, targeting, BI và API
- Output: ERD, dbt lineage, metric dictionary, Gold marts có test
- Xong khi: `dbt build` xanh, không cộng trùng spend và KPI có công thức, grain, đơn vị rõ

### Tuần 6 — Data quality và backfill

- Input: Bronze, Silver, Gold
- Công nghệ: dbt tests, pytest, SQL assertions
- Việc làm: kiểm tra uniqueness, null, freshness, spend âm, schema drift, source correction và backfill 2–4 kỳ
- Output: DQ scorecard, audit table, quarantine reason, bằng chứng rerun
- Xong khi: lỗi quan trọng chặn publish, backfill không làm mất lịch sử hoặc tạo duplicate

### Tuần 7 — Airflow local

- Input: các CLI của ingestion, dbt và quality
- Công nghệ: Airflow một worker
- Việc làm: DAG `extract → load → transform → quality → publish`, retry, timeout, backfill và mô phỏng lỗi
- Output: DAG Graph/Grid, log run và runbook sự cố
- Xong khi: chạy lại một logical date vẫn ra cùng kết quả

### Tuần 8 — Chạy batch trên cloud

- Input: các task đã chạy được bằng CLI
- Công nghệ: Cloud Run Jobs, Workflows, Scheduler, Terraform, IAM
- Việc làm: deploy batch job, quality gate, lịch chạy có thể pause, log và quyền tối thiểu
- Output: cloud run IDs, deployment diagram, IAM matrix và ước tính chi phí
- Xong khi: chạy nguồn đến Gold không cần Mac bật, lỗi hiện trong log và lịch có thể tắt

### Tuần 9 — Phân tích business và thống kê

- Input: Gold marts
- Công nghệ: SQL, pandas trên aggregate, Plotly
- Việc làm: phân tích trend, WoW, top-share, HHI, phân phối spend, targeting và advertiser tăng/giảm mạnh
- Output: 3–5 insight có SQL kiểm chứng, biểu đồ, wireframe Power BI và web
- Xong khi: mỗi insight có kỳ so sánh, số đo, nguồn và giới hạn suy luận

### Tuần 10 — Power BI Cloud

- Input: Gold marts và metric dictionary
- Công nghệ: Power BI Cloud, BigQuery connector, Power Query, DAX
- Việc làm: trang Executive, Advertiser, Geography & Targeting, Data Quality, slicer và drill-through
- Output: report, ảnh chụp, bảng đối soát ít nhất 3 KPI với SQL
- Xong khi: refresh hoặc snapshot được gắn nhãn đúng, số Power BI khớp SQL cùng filter

### Tuần 11 — ML baseline và temporal validation

- Input: fact advertiser-weekly hoặc target fallback
- Công nghệ: SQL, scikit-learn, LightGBM, MLflow
- Việc làm: tạo lag/rolling features không leakage, rolling-origin folds, naive baseline, LightGBM point và quantile
- Output: feature table, fold manifest, leaderboard đầu tiên
- Xong khi: mọi model dùng cùng test folds và có MAE, WAPE/MASE, pinball, coverage

### Tuần 12 — Deep learning và model hiện đại

- Input: folds và cohort từ tuần 11
- Công nghệ: PyTorch Forecasting TFT nhỏ, Chronos-2-small zero-shot, MLflow
- Việc làm: so naive, LightGBM, TFT, Chronos theo accuracy, interval coverage, runtime và peak RAM
- Output: leaderboard 4 nhóm model, actual-vs-forecast, residual chart, interval plot, model card
- Xong khi: model thắng baseline theo ngưỡng đặt trước, nếu không giữ baseline

### Tuần 13 — Registry, batch forecast và drift

- Input: model được chọn và Gold mart mới nhất
- Công nghệ: MLflow, BigQuery, Airflow hoặc Cloud Run Job
- Việc làm: lưu model version, batch score sau quality gate, `MERGE` forecast mart, theo dõi lỗi khi actual xuất hiện
- Output: forecast mart có p10/p50/p90, alert spike, bảng drift và trang forecast trong BI
- Xong khi: rerun không duplicate, biết forecast thuộc model và data cutoff nào

### Tuần 14 — FastAPI cho platform

- Input: Gold, forecast và DQ marts
- Công nghệ: FastAPI, Pydantic, BigQuery client, pytest
- Việc làm: API read-only cho overview, advertisers, trends, geography, forecasts và pipeline health
- Việc làm thêm: response schema, filter whitelist, pagination, timeout, query cost limit, cache, CORS, request ID, test với fixture offline
- Output: `apps/api/`, OpenAPI docs, JSON examples, unit/integration tests
- Xong khi: ít nhất 3 KPI của API khớp SQL và Power BI, không lộ raw data hoặc credential

### Tuần 15 — React web platform

- Input: API và wireframe business
- Công nghệ: React, TypeScript, Vite, TanStack Query, Apache ECharts, Playwright
- Việc làm: 5 màn hình Overview, Advertiser Explorer, Geography & Targeting, Forecast & Anomalies, Pipeline Health
- Việc làm thêm: filter đồng bộ URL, drill-down, loading/empty/error states, freshness label, chart có đơn vị và caveat
- Output: `apps/web/`, giao diện responsive, ảnh/video demo, UI và end-to-end tests
- Xong khi: web dùng API thay vì gọi BigQuery trực tiếp, KPI khớp SQL và Power BI, phân biệt rõ sample/snapshot/live

### Tuần 16 — Kiểm thử toàn luồng và portfolio

- Input: pipeline, BI, model, API và web
- Công nghệ: CI, BigQuery job stats, Cloud logs, Terraform, Mermaid
- Việc làm: chạy source → Gold → BI/forecast → API → web, thử retry/backfill/schema drift/lỗi API
- Việc làm thêm: đo bytes, thời gian, RAM, API p50/p95, chi phí, kiểm tra quyền và quét secrets
- Output: architecture diagram, DAG, ERD, lineage, ML lifecycle, benchmark table, runbook, demo 5–7 phút, CV bullets
- Xong khi: người khác clone được và chạy smoke test không cần secret, có bằng chứng KPI khớp SQL–BI–API–web
