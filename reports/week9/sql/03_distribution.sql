WITH bucketed AS (
            SELECT
                week_start_date,
                CASE
                    WHEN amount_usd < 1000 THEN 1
                    WHEN amount_usd < 10000 THEN 2
                    WHEN amount_usd < 100000 THEN 3
                    WHEN amount_usd < 1000000 THEN 4
                    ELSE 5
                END AS bucket_order,
                CASE
                    WHEN amount_usd < 1000 THEN '< 1k USD'
                    WHEN amount_usd < 10000 THEN '1k–10k USD'
                    WHEN amount_usd < 100000 THEN '10k–100k USD'
                    WHEN amount_usd < 1000000 THEN '100k–1m USD'
                    ELSE '≥ 1m USD'
                END AS spend_bucket
            FROM fct_advertiser_weekly_spend
        )
        SELECT
            week_start_date,
            bucket_order,
            spend_bucket,
            COUNT(*) AS advertiser_count
        FROM bucketed
        GROUP BY week_start_date, bucket_order, spend_bucket
        ORDER BY week_start_date, bucket_order
