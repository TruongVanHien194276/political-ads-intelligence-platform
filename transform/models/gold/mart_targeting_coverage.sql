SELECT
    'creative_stats' AS required_source,
    'NOT_INGESTED' AS data_status,
    FALSE AS targeting_metrics_ready,
    DATE '2026-10-17' AS source_checked_on,
    'Silver hiện chỉ có advertiser_weekly_spend' AS reason,
    MAX(week_start_date) AS latest_loaded_week
FROM {{ ref('fct_advertiser_weekly_spend') }}

UNION ALL

SELECT
    'campaign_targeting' AS required_source,
    'SOURCE_EMPTY_AT_LAST_CHECK' AS data_status,
    FALSE AS targeting_metrics_ready,
    DATE '2026-10-17' AS source_checked_on,
    'Bảng nguồn rỗng theo inventory Tuần 1' AS reason,
    MAX(week_start_date) AS latest_loaded_week
FROM {{ ref('fct_advertiser_weekly_spend') }}
