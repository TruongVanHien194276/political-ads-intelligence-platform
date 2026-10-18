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
- Chưa chạy `terraform apply`; ingestion raw lake và các phần sau thuộc Tuần 3 trở đi
