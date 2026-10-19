SELECT
    advertiser_id,
    advertiser_name,
    total_spend_usd,
    weeks_observed,
    last_observed_week,
    share_of_observed_spend_pct,
    ROW_NUMBER() OVER (
        ORDER BY total_spend_usd DESC, advertiser_id
    ) AS spend_rank
FROM {{ ref('mart_advertiser_summary') }}