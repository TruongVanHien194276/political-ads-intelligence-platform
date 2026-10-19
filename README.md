# Political Ads Intelligence Platform

- Project portfolio end-to-end về dữ liệu quảng cáo chính trị công khai của Google
- Mục tiêu: xây pipeline dữ liệu, warehouse, Power BI, phân tích business, ML forecast và web platform để demo
- Nguồn: [Google Political Ads public dataset](https://cloud.google.com/blog/topics/developers-practitioners/how-get-started-political-ads-transparency-report-dataset) qua BigQuery API
- Phạm vi đầu tiên: chuỗi spend USD theo advertiser-tuần; phân tích địa lý Mỹ từ bảng geo riêng
- Đầu ra: Gold marts, dashboard Power BI, insight report, model leaderboard, FastAPI và React web
- Máy phát triển: MacBook Pro M3, 8 GB RAM, xử lý nặng trên BigQuery, local chỉ chạy từng phần cần thiết
- Google Cloud project: `political-ads-intel-194276`, BigQuery Sandbox, chưa gắn billing
- Môi trường local: Python 3.14.4 trong `.venv`, BigQuery client và ADC đã đọc được metadata nguồn
- [Roadmap 16 tuần với công việc và output từng tuần](ROADMAP.md)
- [Tuần 1: source inventory, profiling và quyết định ML](reports/week1/WEEK1.md) đã hoàn thành
- Tuần 2 đã có package Python, CLI dry-run có cost limit, mock tests, Docker, CI và Terraform plan
- Kiểm tra offline bằng `python -m pip install -e ".[dev]"` rồi `make check`
- Tuần 3 đã có bốn snapshot tuần 2026-09-20 đến 2026-10-11 trong `data/raw/`, đã đối soát và publish sang `data/tmp/raw-lake/` (9.699 dòng tổng cộng)
- Kiểm tra backfill không tải thêm dữ liệu: `political-ads-raw --backfill-start 2026-09-20 --backfill-end 2026-10-11`; cùng phiên bản nguồn sẽ trả `SKIPPED`, nguồn đổi sẽ trả `DRY_RUN` cho phiên bản mới
- Publish một snapshot: `political-ads-publish-local --source-run data/raw/source=google_political_ads/table=advertiser_weekly_spend/week_start=2026-10-04/run_id=f6c333bcf330ef62`
- Dữ liệu raw và lake local được `.gitignore` để không đưa lên Git; GCS chưa được upload và `terraform apply` chưa chạy vì project không bật billing

## Tuần 4 — Bronze và Silver

- Dùng Python 3.13 riêng cho dbt-duckdb trong `data/tmp/week4/venv`; Python 3.14 của Tuần 1–3 không đổi
- Cài môi trường: `python3.13 -m venv data/tmp/week4/venv` rồi `data/tmp/week4/venv/bin/python -m pip install 'duckdb==1.5.6' 'dbt-core>=1.11,<1.12' 'dbt-duckdb==1.11.0'`
- Sau khi cài môi trường, chạy `make week4` từ project root để lần lượt load Bronze, dbt build và MERGE Silver current
- DuckDB local có Bronze append-only, dbt staging/valid/quarantine, Silver history là lịch sử snapshot, Silver current dùng `MERGE` theo advertiser-tuần; replay không nhân đôi key
- Đối soát trên bốn snapshot hiện tại: raw 9.699 = valid 9.699 + quarantine 0; history 9.699; current 9.699; duplicate current key 0
- BigQuery Sandbox chỉ có bảng demo `political_ads_bronze_demo.raw_f6c333bcf330ef62` với 2.629 dòng, không chạy `MERGE` trên BigQuery Sandbox; script demo từ chối project đã bật billing
- Kiểm tra offline bằng `make check`; test có replay, sửa/xóa nguồn, checksum và quality gate

## Tuần 5 — Warehouse và dbt Gold

- Chạy `make week5` từ project root để refresh Bronze/Silver rồi build Gold; dbt-duckdb giới hạn 2 threads và 1 GB RAM
- Gold có dimension advertiser/tuần, fact advertiser-tuần USD, mart trend, advertiser, BI và API; targeting chỉ có mart trạng thái nguồn vì chưa có Silver targeting
- Xem [ERD, lineage, metric dictionary và cách kiểm tra](reports/week5/WEEK5.md)
- dbt test đối chiếu spend từ Silver đến từng mart, kiểm tra key, quan hệ dimension, spend âm và ngày đầu tuần

## Tuần 6 — Data quality và backfill

- Chạy `make week6` để build trên candidate warehouse và chỉ publish khi schema, dbt tests và DQ scorecard đều đạt
- Audit, quarantine reason, backup và bằng chứng rerun bốn kỳ được ghi trong [báo cáo Tuần 6](reports/week6/WEEK6.md)
- Test offline kiểm tra source correction giữ history, schema drift, quarantine và lỗi critical không thay Silver/Gold đang dùng

## Tuần 7 — Airflow local

- DAG `political_ads_weekly` điều phối 5 bước trên raw snapshot local, giới hạn một task cùng lúc
- Cách khởi động, backfill, retry, replay và xử lý sự cố: [runbook Tuần 7](reports/week7/WEEK7.md)
