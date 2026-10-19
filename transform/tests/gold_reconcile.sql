-- Test trả về 0 dòng thì pass
WITH silver AS (
    SELECT
        COUNT(*) AS row_count,
        SUM(amount_usd) AS spend_usd
    FROM {{ source('silver', 'weekly_current') }}
    WHERE currency_code = 'USD'
),

fact AS (
    SELECT
        COUNT(*) AS row_count,
        SUM(amount_usd) AS spend_usd
    FROM {{ ref('fct_advertiser_weekly_spend') }}
),

weekly_mart AS (
    SELECT SUM(total_spend_usd) AS spend_usd
    FROM {{ ref('mart_spend_trend_weekly') }}
),

advertiser_mart AS (
    SELECT SUM(total_spend_usd) AS spend_usd
    FROM {{ ref('mart_advertiser_summary') }}
),

bi_mart AS (
    SELECT SUM(total_spend_usd) AS spend_usd
    FROM {{ ref('mart_bi_weekly') }}
),

api_mart AS (
    SELECT SUM(total_spend_usd) AS spend_usd
    FROM {{ ref('mart_api_advertiser') }}
)

SELECT
    silver.row_count AS silver_rows,
    fact.row_count AS fact_rows,
    silver.spend_usd AS silver_spend,
    fact.spend_usd AS fact_spend,
    weekly_mart.spend_usd AS weekly_mart_spend,
    advertiser_mart.spend_usd AS advertiser_mart_spend,
    bi_mart.spend_usd AS bi_mart_spend,
    api_mart.spend_usd AS api_mart_spend
FROM silver
CROSS JOIN fact
CROSS JOIN weekly_mart
CROSS JOIN advertiser_mart
CROSS JOIN bi_mart
CROSS JOIN api_mart
WHERE silver.row_count = 0
   OR silver.row_count IS DISTINCT FROM fact.row_count
   OR silver.spend_usd IS DISTINCT FROM fact.spend_usd
   OR fact.spend_usd IS DISTINCT FROM weekly_mart.spend_usd
   OR fact.spend_usd IS DISTINCT FROM advertiser_mart.spend_usd
   OR fact.spend_usd IS DISTINCT FROM bi_mart.spend_usd
   OR fact.spend_usd IS DISTINCT FROM api_mart.spend_usd
