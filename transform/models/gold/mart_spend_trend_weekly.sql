WITH weekly AS (
    SELECT
        week_start_date,
        SUM(amount_usd) AS total_spend_usd,
        COUNT(*) AS advertiser_count
    FROM {{ ref('fct_advertiser_weekly_spend') }}
    GROUP BY week_start_date
),

with_previous AS (
    SELECT
        *,
        LAG(week_start_date) OVER (
            ORDER BY week_start_date
        ) AS previous_week,
        LAG(total_spend_usd) OVER (
            ORDER BY week_start_date
        ) AS previous_spend_usd
    FROM weekly
)

SELECT
    week_start_date,
    total_spend_usd,
    advertiser_count,
    previous_spend_usd,
    CASE
        WHEN DATE_DIFF('day', previous_week, week_start_date) = 7
             AND previous_spend_usd > 0
        THEN ROUND(
            100.0 * (total_spend_usd / previous_spend_usd - 1),
            2
        )
        ELSE NULL
    END AS wow_change_pct
FROM with_previous