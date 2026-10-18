SELECT
    run_id,
    advertiser_id_norm,
    week_start_norm,
    count(*) AS n
FROM {{ ref('valid_weekly') }}
GROUP BY 1, 2, 3
HAVING count(*) > 1