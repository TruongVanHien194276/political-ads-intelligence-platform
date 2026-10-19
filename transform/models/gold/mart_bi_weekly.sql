SELECT
    t.week_start_date,
    w.week_end_date,
    w.calendar_year,
    w.calendar_month,
    t.total_spend_usd,
    t.advertiser_count,
    t.previous_spend_usd,
    t.wow_change_pct
FROM {{ ref('mart_spend_trend_weekly') }} AS t
JOIN {{ ref('dim_week') }} AS w
    USING (week_start_date)