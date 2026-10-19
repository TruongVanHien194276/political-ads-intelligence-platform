SELECT DISTINCT
    week_start_date,
    week_start_date + INTERVAL 6 DAY AS week_end_date,
    EXTRACT(YEAR FROM week_start_date) AS calendar_year,
    EXTRACT(MONTH FROM week_start_date) AS calendar_month
FROM {{ source('silver', 'weekly_current') }}