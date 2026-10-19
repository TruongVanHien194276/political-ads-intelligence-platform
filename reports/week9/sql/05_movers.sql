WITH periods AS (
            SELECT
                MAX(week_start_date) AS current_week,
                MAX(week_start_date) - 7 AS previous_week
            FROM mart_spend_trend_weekly
        ),
        paired AS (
            SELECT
                f.advertiser_id,
                MAX(f.amount_usd) FILTER (
                    WHERE f.week_start_date = p.previous_week
                ) AS previous_spend_usd,
                MAX(f.amount_usd) FILTER (
                    WHERE f.week_start_date = p.current_week
                ) AS current_spend_usd
            FROM fct_advertiser_weekly_spend AS f
            CROSS JOIN periods AS p
            WHERE f.week_start_date IN (
                p.previous_week, p.current_week
            )
            GROUP BY f.advertiser_id
        )
        SELECT
            x.advertiser_id,
            d.advertiser_name,
            x.previous_spend_usd,
            x.current_spend_usd,
            CASE
                WHEN x.previous_spend_usd IS NOT NULL
                 AND x.current_spend_usd IS NOT NULL
                    THEN 'both_weeks'
                WHEN x.previous_spend_usd IS NULL
                    THEN 'newly_observed'
                ELSE 'not_observed_current'
            END AS cohort,
            CASE
                -- Chỉ tính thay đổi khi có quan sát ở cả hai tuần
                WHEN x.previous_spend_usd IS NOT NULL
                 AND x.current_spend_usd IS NOT NULL
                    THEN x.current_spend_usd - x.previous_spend_usd
                ELSE NULL
            END AS change_usd,
            CASE
                WHEN x.previous_spend_usd > 0
                 AND x.current_spend_usd IS NOT NULL
                    THEN ROUND(
                        100.0 * (
                            x.current_spend_usd / x.previous_spend_usd - 1
                        ),
                        2
                    )
                ELSE NULL
            END AS change_pct
        FROM paired AS x
        LEFT JOIN dim_advertiser AS d
            USING (advertiser_id)
        ORDER BY ABS(change_usd) DESC NULLS LAST, x.advertiser_id
