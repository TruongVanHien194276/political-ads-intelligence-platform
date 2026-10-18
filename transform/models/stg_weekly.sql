WITH currency_checks AS (
    -- UNPIVOT chỉ dùng để kiểm tra tất cả cột spend_*
    -- Không đổi grain của staging: kết quả được gom lại theo dòng Bronze
    SELECT
        run_id,
        row_in_run,
        min(try_cast(amount AS DECIMAL(18, 2))) AS min_currency_amount
    FROM (
        UNPIVOT {{ source('bronze', 'weekly_raw') }}
        ON COLUMNS('^spend_')
        INTO NAME currency_column VALUE amount
    )
    GROUP BY run_id, row_in_run
),

typed AS (
    SELECT
        b.*,
        b.run_id || ':' || cast(b.row_in_run AS VARCHAR) AS bronze_row_key,
        nullif(trim(b.advertiser_id), '') AS advertiser_id_norm,
        try_cast(b.week_start_date AS DATE) AS week_start_norm,
        try_cast(b.spend_usd AS DECIMAL(18, 2)) AS amount_usd,
        row_number() OVER (
            PARTITION BY b.run_id, b.advertiser_id, b.week_start_date
            ORDER BY b.row_in_run
        ) AS duplicate_rank
    FROM {{ source('bronze', 'weekly_raw') }} AS b
)

SELECT
    t.*,
    'USD' AS currency_code,
    CASE
        WHEN t.advertiser_id_norm IS NULL
            THEN 'missing_advertiser_id'
        WHEN t.week_start_norm IS NULL
            THEN 'invalid_week_start'
        WHEN strftime(t.week_start_norm, '%w') <> '0'
            THEN 'week_not_sunday'
        WHEN t.week_start_norm <> t.snapshot_week_start
            THEN 'week_mismatch_manifest'
        WHEN t.amount_usd IS NULL
            THEN 'missing_or_invalid_spend_usd'
        WHEN t.amount_usd < 0 OR c.min_currency_amount < 0
            THEN 'negative_spend'
        WHEN t.duplicate_rank > 1
            THEN 'duplicate_key_in_run'
        ELSE NULL
    END AS validation_error
FROM typed AS t
LEFT JOIN currency_checks AS c
    USING (run_id, row_in_run)