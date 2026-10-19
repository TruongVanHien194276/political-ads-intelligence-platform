-- Chặn spend âm hoặc một tuần không bắt đầu vào Chủ nhật
SELECT
    advertiser_week_key,
    advertiser_id,
    week_start_date,
    amount_usd
FROM {{ ref('fct_advertiser_weekly_spend') }}
WHERE amount_usd < 0
   OR STRFTIME(week_start_date, '%w') <> '0'
