SELECT *
FROM {{ ref('stg_weekly') }}
WHERE validation_error IS NULL