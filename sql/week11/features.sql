WITH weeks AS (
    SELECT DISTINCT week_start_date
    FROM fct_advertiser_weekly_spend
),

bounds AS (
    SELECT MIN(week_start_date) AS first_week
    FROM weeks
),

cohort AS (
    SELECT
        f.advertiser_id,
        SUM(f.amount_usd) AS selection_spend
    FROM fct_advertiser_weekly_spend AS f
    CROSS JOIN bounds AS b
    -- Chỉ chọn cohort bằng 8 tuần đầu, không nhìn vào các test weeks
    WHERE f.week_start_date < b.first_week + INTERVAL 8 WEEK
    GROUP BY f.advertiser_id
    HAVING COUNT(DISTINCT f.week_start_date) = 8
    ORDER BY selection_spend DESC, f.advertiser_id
    LIMIT 100
),

grid AS (
    SELECT
        c.advertiser_id,
        w.week_start_date
    FROM cohort AS c
    CROSS JOIN weeks AS w
),

panel AS (
    SELECT
        g.advertiser_id,
        g.week_start_date,
        CAST(f.amount_usd AS DOUBLE) AS y
    FROM grid AS g
    LEFT JOIN fct_advertiser_weekly_spend AS f
        ON g.advertiser_id = f.advertiser_id
       AND g.week_start_date = f.week_start_date
),

features AS (
    SELECT
        advertiser_id,
        week_start_date,
        y,

        -- Với grid đủ mọi tuần, LAG(1) đúng là tuần lịch trước
        LAG(y, 1) OVER series AS lag_1,
        LAG(y, 2) OVER series AS lag_2,
        LAG(y, 4) OVER series AS lag_4,

        AVG(y) OVER (
            PARTITION BY advertiser_id
            ORDER BY week_start_date
            ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
        ) AS rolling_mean_4,

        COUNT(y) OVER (
            PARTITION BY advertiser_id
            ORDER BY week_start_date
            ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
        ) AS observed_previous_4
    FROM panel
    WINDOW series AS (
        PARTITION BY advertiser_id
        ORDER BY week_start_date
    )
)

SELECT
    advertiser_id,
    week_start_date,
    y,
    lag_1,
    lag_2,
    lag_4,
    rolling_mean_4
FROM features
WHERE y IS NOT NULL
  AND observed_previous_4 = 4
  AND lag_1 IS NOT NULL
  AND lag_2 IS NOT NULL
  AND lag_4 IS NOT NULL
ORDER BY week_start_date, advertiser_id