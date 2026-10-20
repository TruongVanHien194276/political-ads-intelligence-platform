# Political Ads Intelligence Platform

Pipeline phân tích quảng cáo chính trị từ [Google Political Ads public dataset](https://cloud.google.com/blog/topics/developers-practitioners/how-get-started-political-ads-transparency-report-dataset)

- Dữ liệu chính: spend USD theo advertiser × tuần, 20 tuần đã tải, 31.453 fact rows
- Pipeline: BigQuery extractor → raw Parquet/manifest → DuckDB Bronze/Silver → dbt Gold → BI snapshot, forecast, API và React
- Kiểm soát: idempotent run ID, checksum, quarantine, source correction, quality gate, Airflow local, pytest, Playwright, CI và Terraform
- ML: so naive, LightGBM, TFT nhỏ và Chronos-2-small trên cùng 578 test rows qua 4 rolling folds; naive giữ champion vì challenger không qua promotion gate
- Máy chạy: Mac M3 8 GB, mỗi lúc chạy một phần nặng, dbt 2 threads/1 GB, web và API chạy local

## Xem nhanh

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,api]'
cd apps/web && npm ci && npx playwright install chromium && cd ../..
make smoke
```

`make smoke` chạy Python tests, lint/build React và Playwright fixture, không cần Google Cloud key, raw data hay billing

- [Hướng dẫn chạy từ đầu đến cuối](RUNBOOK.md)
- [Kiến trúc, số đo, biểu đồ và bằng chứng](reports/EVIDENCE.md)
- [Ảnh web Overview](reports/week15/screenshots/01_overview_local.png)
- [Bằng chứng kiểm thử cuối](reports/week16/evidence.json)

## Trạng thái thật

| Phần | Trạng thái |
| --- | --- |
| Raw → Bronze/Silver/Gold local | Đã chạy lại 20 snapshot, 16/16 DQ PASS |
| Excel snapshot cho BI | 6/6 KPI khớp SQL, file export local không commit |
| Forecast, API, React | Đã chạy local, 3 KPI SQL–API–web khớp |
| Power BI Cloud report | Chưa nghiệm thu trên đúng tài khoản, không nhận là đã khớp |
| Cloud Run/Workflows/Scheduler | Terraform và mock tests xong, chưa deploy vì project chưa bật billing |
| Geo/targeting | Chưa có mart đủ tin cậy, giao diện nêu rõ thiếu dữ liệu |

Nguồn weekly không có country, click, conversion hay revenue nên không gọi đây là US-only spend và không tính CTR/ROAS

Repo không chứa raw Parquet, warehouse, model weights, workbook xuất ra hoặc credential; clone mới chạy smoke offline, còn demo dữ liệu thật cần tạo lại snapshot theo [runbook](RUNBOOK.md)
