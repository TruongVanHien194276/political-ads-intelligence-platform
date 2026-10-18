WITH raw_counts AS (
    SELECT run_id, count(*) AS n
    FROM {{ source('bronze', 'weekly_raw') }}
    GROUP BY 1
),
valid_counts AS (
    SELECT run_id, count(*) AS n
    FROM {{ ref('valid_weekly') }}
    GROUP BY 1
),
bad_counts AS (
    SELECT run_id, count(*) AS n
    FROM {{ ref('quarantine_weekly') }}
    GROUP BY 1
)

SELECT
    r.run_id,
    r.n AS raw_rows,
    coalesce(v.n, 0) AS valid_rows,
    coalesce(b.n, 0) AS quarantine_rows
FROM raw_counts r
LEFT JOIN valid_counts v USING (run_id)
LEFT JOIN bad_counts b USING (run_id)
WHERE r.n <> coalesce(v.n, 0) + coalesce(b.n, 0)