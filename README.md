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

## Tuần 8 — Batch cloud (đã chuẩn bị offline)

- Có CLI riêng cho Cloud Run Jobs, image, Terraform cho IAM/Jobs/Workflows/Scheduler và SQL publish Bronze → Silver → Gold
- `make check`, `terraform validate` và image smoke test chạy offline; chưa deploy vì project không bật billing theo lựa chọn của chủ project
- Lịch Terraform mặc định `paused = true`, runtime mặc định tắt; chưa có cloud run IDs hoặc bằng chứng chạy nguồn đến Gold không cần Mac
- Sơ đồ triển khai, IAM, chi phí, runbook và tiêu chí nghiệm thu cloud: [Tuần 8](reports/week8/WEEK8.md)

## Tuần 9 — Business insights

- Chạy `make week9` sau khi đã có Gold local để tạo SQL, evidence JSON, 5 biểu đồ PNG và HTML tương tác
- [Báo cáo Tuần 9](reports/week9/WEEK9.md) có 5 insight, kỳ so sánh, số đo, SQL và giới hạn suy luận
- Biểu đồ tĩnh được commit; HTML và Plotly.js tạo lại local để tránh đưa bundle lớn vào Git

## Tuần 10 — Power BI Cloud (chưa nghiệm thu cloud)

- `make week10` tạo Excel snapshot local, đối soát 6 KPI với Gold và lưu biểu đồ + evidence có thể xem trên GitHub
- [Báo cáo Tuần 10](reports/week10/WEEK10.md) có mô hình, DAX, thiết kế 4 trang và ghi rõ phần Power BI chưa làm trên đúng tài khoản
- Chưa upload vào Power BI vì phiên browser hiện tại đăng nhập tài khoản khác; chưa có report hoặc screenshot Power BI thật

## Tuần 11 — ML baseline và temporal validation

- Gold đã có 20 tuần liên tiếp; `make week11` chạy rolling one-week-ahead backtest với 4 folds, naive và LightGBM point/quantile trong venv Python 3.13 riêng
- [Báo cáo Tuần 11](reports/week11/WEEK11.md) có leaderboard, 4 biểu đồ và [evidence JSON](reports/week11/evidence.json) với hash nguồn/SQL/predictions
- `make week12` so naive, LightGBM, TFT nhỏ và Chronos-2-small trên đúng 578 test rows; [báo cáo Tuần 12](reports/week12/WEEK12.md) có model card, 3 biểu đồ và [evidence JSON](reports/week12/evidence.json); chưa model mới nào qua promotion gate nên giữ naive
- Naive thắng LightGBM về MAE/WAPE trên cùng 578 test rows; đây là baseline thử nghiệm, không phải model production
- Workbook Power BI Tuần 10 được export trước backfill nên cần tạo lại nếu tiếp tục dashboard

## Tuần 13 — Registry, batch forecast và drift

- `make week13` chạy local quality-gated MLflow registration, batch score, monitor và lưu bằng chứng; dùng Python 3.13 venv Tuần 11
- [Báo cáo Tuần 13](reports/week13/WEEK13.md) có model card, forecast mart, 3 biểu đồ PNG và [evidence JSON](reports/week13/evidence.json)
- Live cutoff 2026-10-11 dự đoán tuần 2026-10-18 cho 73 advertiser; 27 advertiser thiếu dữ liệu được abstain, actual live chưa có trong Gold
- Power BI Cloud Forecast page chưa tạo trên đúng tài khoản; ảnh đã lưu chỉ là biểu đồ local, không phải screenshot Power BI

## Tuần 14 — FastAPI local

- Cài API bằng `.venv/bin/python -m pip install -e '.[dev,api]'`, rồi chạy `make week14`
- Server: `.venv/bin/python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000`; tài liệu tại `http://127.0.0.1:8000/docs`
- [Báo cáo, sơ đồ luồng và giới hạn nghiệm thu](reports/week14/WEEK14.md); [bằng chứng đối soát API–SQL](reports/week14/evidence.json) và [ví dụ JSON](reports/week14/examples.json)
- API hiện đọc DuckDB local, không gọi BigQuery; chưa thể xác nhận API khớp Power BI Cloud vì report Power BI chưa được nghiệm thu

## Tuần 15 — React web platform

- Web local ở `apps/web/`, dùng Vite proxy gọi FastAPI, không truy vấn BigQuery từ browser
- Chạy API như Tuần 14, sau đó `cd apps/web && npm ci && npm run dev` và mở `http://127.0.0.1:5173`
- `npm run build`, `npm run lint`, `npm run test:e2e` chạy với fixture; `npm run test:live` cần API thật và tạo ảnh + đối soát SQL–API–web
- [Báo cáo và ảnh chụp 5 màn hình, mobile](reports/week15/WEEK15.md) · [evidence JSON](reports/week15/evidence.json)
- Power BI Cloud vẫn chưa đối soát được trên đúng tài khoản; không gắn nhãn hoàn thành KPI bốn phía
