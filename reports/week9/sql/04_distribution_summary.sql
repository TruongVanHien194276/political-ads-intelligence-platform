SELECT
            week_start_date,
            COUNT(*) AS advertiser_count,
            MEDIAN(amount_usd) AS median_spend_usd,
            QUANTILE_CONT(amount_usd, 0.90) AS p90_spend_usd,
            QUANTILE_CONT(amount_usd, 0.99) AS p99_spend_usd,
            MAX(amount_usd) AS max_spend_usd
        FROM fct_advertiser_weekly_spend
        GROUP BY week_start_date
        ORDER BY week_start_date
