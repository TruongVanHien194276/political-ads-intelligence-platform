{{ config(materialized='table') }}

-- Đây là lịch sử các lần quan sát snapshot, chưa phải SCD2 valid_from/valid_to
-- Cùng advertiser-tuần có thể xuất hiện ở nhiều run_id nếu nguồn được sửa
SELECT *
FROM {{ ref('valid_weekly') }}