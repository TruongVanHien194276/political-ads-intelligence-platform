SELECT
            week_start_date,
            total_spend_usd,
            advertiser_count,
            previous_spend_usd,
            wow_change_pct
        FROM mart_spend_trend_weekly
        ORDER BY week_start_date
