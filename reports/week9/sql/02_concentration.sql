WITH ranked AS (
            SELECT
                week_start_date,
                advertiser_id,
                amount_usd,
                SUM(amount_usd) OVER (
                    PARTITION BY week_start_date
                ) AS week_total_usd,
                ROW_NUMBER() OVER (
                    PARTITION BY week_start_date
                    ORDER BY amount_usd DESC, advertiser_id
                ) AS spend_rank
            FROM fct_advertiser_weekly_spend
        )
        SELECT
            week_start_date,
            ROUND(
                100.0 * SUM(
                    CASE WHEN spend_rank <= 10 THEN amount_usd ELSE 0 END
                ) / NULLIF(MAX(week_total_usd), 0),
                2
            ) AS top10_share_pct,
            ROUND(
                10000.0 * SUM(
                    POWER(amount_usd / NULLIF(week_total_usd, 0), 2)
                ),
                2
            ) AS observed_hhi
        FROM ranked
        GROUP BY week_start_date
        ORDER BY week_start_date
