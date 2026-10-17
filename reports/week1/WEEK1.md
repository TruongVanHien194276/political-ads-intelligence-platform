# Tuần 1 — kiểm tra nguồn

- Nguồn: `bigquery-public-data.google_political_ads`, location `US`
- Kiểm tra ngày 2026-10-17 bằng BigQuery API và SQL
- `last_updated.report_data_updated_time`: 2026-10-17 06:04:29 UTC tại lần kiểm tra
- Có 9 bảng, trong đó `campaign_targeting` và `top_keywords_history` đang rỗng
- Schema đầy đủ nằm trong `source_inventory.json` và `02_columns.json`

## Data dictionary v1

| Bảng | Grain / khóa ứng viên | Trường cần dùng | Cách hiểu và giới hạn |
| --- | --- | --- | --- |
| `advertiser_weekly_spend` | `advertiser_id` + `week_start_date` tại snapshot hiện tại | `advertiser_id` STRING, `week_start_date` DATE, `spend_usd` INT64, các cột `spend_<currency>` INT64 | 315.944 dòng, không có country hoặc region, `election_cycle` null toàn bộ; không gọi đây là spend theo tuần của riêng Mỹ |
| `advertiser_stats` | `advertiser_id` ứng viên, cần test riêng khi ingest | `advertiser_name`, `regions`, `total_creatives`, `spend_usd` và các cột tiền tệ | Tổng hợp theo advertiser, không cộng với bảng weekly hay creative để tránh double count |
| `advertiser_geo_spend` | advertiser + country + subdivision là grain cần xác minh ở Tuần 4 | `country`, `country_subdivision_primary`, `spend_usd` | Tổng theo địa lý, không có tuần; dùng để phân tích phân bố, không lấy làm nhãn forecast tuần |
| `creative_stats` | `ad_id` duy nhất tại snapshot hiện tại | `date_range_start`, `date_range_end`, `ad_type`, `impressions` STRING, `spend_range_min_usd`, `spend_range_max_usd` INT64 | Spend/impressions dạng chuỗi hoặc khoảng; min/max là biên công bố, không phải spend chính xác; 13 dòng có min > max cần cách ly |
| `last_updated` | Một dòng | `report_data_updated_time` DATETIME | Thời điểm cập nhật báo cáo nguồn, không phải timestamp của từng sự kiện quảng cáo |

## Kết quả profiling

- Weekly: 315.944 dòng, 22.771 advertiser, từ 2018-05-27 đến 2026-10-11
- Weekly: không có null `advertiser_id`, ngày không hợp lệ, spend không hợp lệ hoặc spend âm trong các phép kiểm tra hiện tại
- Có 60.820 dòng `spend_usd = 0`; không tự coi tuần không xuất hiện trong bảng là bằng 0
- Không thấy duplicate theo `advertiser_id` + `week_start_date` trong output kiểm tra hiện tại
- 2.601 advertiser có ít nhất 26 tuần quan sát, 1.162 có ít nhất 52 tuần; 989 có ít nhất 52 tuần trên lịch dài ít nhất 78 tuần
- USD có mặt ở 315.944 dòng, 255.124 dòng `spend_usd > 0`; GBP dương ở 3.787 dòng, EUR dương ở 0 dòng; không cộng tiền khác đơn vị và không giả định `spend_usd` chỉ thuộc Mỹ
- Geo: 687.133 dòng country `US` và 1 dòng `GB`; 17.507 advertiser có US spend dương và 941 trong nhóm này có ít nhất 52 tuần USD dương
- Creative: 1.604.815 dòng và 1.604.815 `ad_id` riêng biệt; 13 dòng có biên spend USD đảo ngược
- `source_inventory.json` dùng `num_rows`/`modified` của bảng, chỉ là metadata snapshot; query profiling là kiểm tra dữ liệu tại thời điểm chạy

## Câu hỏi business có thể trả lời

- Chi tiêu USD theo advertiser tăng/giảm thế nào qua các tuần, xét cùng cửa sổ thời gian
- Top advertiser chiếm bao nhiêu phần tổng USD spend và mức tập trung thay đổi ra sao
- Spend theo địa lý trong bảng geo tập trung ở country/subdivision nào, không gán xu hướng theo tuần cho bảng này
- Cơ cấu ad type, khoảng spend và targeting của creative được công bố ra sao
- Advertiser nào có biến động spend tuần bất thường để analyst kiểm tra thêm, không kết luận nguyên nhân từ nguồn này

Không có click, conversion hoặc doanh thu nên không tính CTR, conversion rate hay ROAS

## Quyết định ML

- Target tạm chọn: dự báo `spend_usd` của advertiser ở tuần kế tiếp bằng chuỗi `advertiser_weekly_spend`
- Forecast theo advertiser, không gọi là forecast spend riêng tại Mỹ vì bảng weekly không có cột địa lý
- Cohort ban đầu: advertiser có ít nhất 52 tuần quan sát; thử riêng nhóm có ít nhất 52 tuần USD dương, tối đa 50–200 advertiser để vừa máy 8 GB RAM
- Chia train/validation/test theo thời gian, so với seasonal naive trước khi thử model phức tạp
- Tuần vắng mặt là missing cho đến khi xác minh semantics; không tự điền 0, không dùng snapshot tổng hợp tương lai làm feature
- Nếu weekly không đủ ổn định sau kiểm tra backtest, chuyển target sang số creative bắt đầu theo tuần và ghi lại quyết định

## Query và chi phí

- `scripts/week1_query.py` luôn dry-run trước; chỉ đọc thật khi có `--execute`
- Mặc định giới hạn 512 MiB bằng `maximum_bytes_billed`; `LIMIT` một mình không phải chặn chi phí
- Cả 10 query đã dry-run thành công; estimate lớn nhất là `06_creative_profile.sql` với 128.385.200 bytes, nhỏ hơn giới hạn
- Chạy lại từng query: `python scripts/week1_query.py --sql sql/week1/03_weekly_profile.sql --execute --out reports/week1/03_weekly_profile.json`
- Các file JSON là snapshot để đối chiếu, không phải dữ liệu nguồn đầy đủ
