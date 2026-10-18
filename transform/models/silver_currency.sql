WITH long_spend AS (
    UNPIVOT {{ ref('valid_weekly') }}
    ON COLUMNS('^spend_')
    INTO NAME spend_column VALUE amount
)

SELECT
    run_id,
    row_in_run,
    advertiser_id_norm AS advertiser_id,
    week_start_norm AS week_start_date,
    upper(replace(spend_column, 'spend_', '')) AS currency_code,
    try_cast(amount AS DECIMAL(18, 2)) AS amount
FROM long_spend
WHERE amount IS NOT NULL