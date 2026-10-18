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
