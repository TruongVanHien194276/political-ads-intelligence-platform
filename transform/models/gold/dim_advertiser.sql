WITH ranked AS (
    SELECT
        advertiser_id,
        COALESCE(
            NULLIF(TRIM(advertiser_name), ''),
            advertiser_id
        ) AS advertiser_name,
        week_start_date AS last_observed_week,

        -- Type 1 dimension: lấy tên mới nhất, không coi đây là lịch sử tên
        ROW_NUMBER() OVER (
            PARTITION BY advertiser_id
            ORDER BY source_modified_utc DESC,
                     week_start_date DESC,
                     run_id DESC
        ) AS rn
    FROM {{ source('silver', 'weekly_current') }}
)

SELECT
    advertiser_id,
    advertiser_name,
    last_observed_week
FROM ranked
WHERE rn = 1