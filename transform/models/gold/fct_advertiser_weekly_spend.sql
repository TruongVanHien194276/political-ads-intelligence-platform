SELECT
    MD5(
        advertiser_id || '|' || CAST(week_start_date AS VARCHAR)
    ) AS advertiser_week_key,
    advertiser_id,
    week_start_date,
    CAST(amount_usd AS DECIMAL(18, 2)) AS amount_usd,
    currency_code,
    run_id,
    source_row_hash
FROM {{ source('silver', 'weekly_current') }}
WHERE currency_code = 'USD'