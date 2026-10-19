WITH advertiser_totals AS (
    SELECT
        advertiser_id,
        MIN(week_start_date) AS first_observed_week,
        MAX(week_start_date) AS last_observed_week,
        COUNT(*) AS weeks_observed,
        SUM(amount_usd) AS total_spend_usd,
        ROUND(AVG(amount_usd), 2) AS avg_observed_week_spend_usd
    FROM {{ ref('fct_advertiser_weekly_spend') }}
    GROUP BY advertiser_id
),

window_total AS (
    SELECT SUM(amount_usd) AS all_advertisers_spend_usd
    FROM {{ ref('fct_advertiser_weekly_spend') }}
)

SELECT
    a.advertiser_id,
    d.advertiser_name,
    a.first_observed_week,
    a.last_observed_week,
    a.weeks_observed,
    a.total_spend_usd,
    a.avg_observed_week_spend_usd,
    ROUND(
        100.0 * a.total_spend_usd
        / NULLIF(w.all_advertisers_spend_usd, 0),
        4
    ) AS share_of_observed_spend_pct
FROM advertiser_totals AS a
JOIN {{ ref('dim_advertiser') }} AS d
    USING (advertiser_id)
CROSS JOIN window_total AS w