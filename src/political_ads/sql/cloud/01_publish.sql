-- Substitute the snapshot table only after strict run_id validation
CREATE TEMP TABLE candidate AS
WITH ranked AS (
  SELECT
    t.*,
    ROW_NUMBER() OVER (
      PARTITION BY t.advertiser_id, t.week_start_date
      ORDER BY TO_JSON_STRING(t)
    ) AS duplicate_rank,
    TO_HEX(SHA256(TO_JSON_STRING(t))) AS source_row_hash
  FROM `${PROJECT}.political_ads_bronze.${SNAPSHOT}` AS t
)
SELECT
  NULLIF(TRIM(t.advertiser_id), '') AS advertiser_id,
  t.week_start_date,
  t.advertiser_name,
  SAFE_CAST(t.spend_usd AS NUMERIC) AS amount_usd,
  'USD' AS currency_code,
  t.source_row_hash,
  @run_id AS run_id,
  @source_modified AS source_modified_utc,
  CASE
    WHEN NULLIF(TRIM(t.advertiser_id), '') IS NULL
      THEN 'missing_advertiser_id'
    WHEN t.week_start_date IS NULL THEN 'missing_week'
    WHEN EXTRACT(DAYOFWEEK FROM t.week_start_date) != 1
      THEN 'week_not_sunday'
    WHEN t.week_start_date != @week_start THEN 'week_mismatch_manifest'
    WHEN t.spend_usd IS NULL THEN 'missing_spend_usd'
    WHEN ${NEGATIVE_ANY} THEN 'negative_spend'
    WHEN t.duplicate_rank > 1 THEN 'duplicate_key_in_run'
    ELSE NULL
  END AS validation_reason
FROM ranked AS t;

ASSERT (SELECT COUNT(*) FROM candidate) = @expected_rows
  AS 'Bronze row count differs from manifest';

-- A failed run leaves auditable bad rows but cannot touch published Silver/Gold
DELETE FROM `${PROJECT}.political_ads_ops.quarantine`
WHERE run_id = @run_id;

INSERT INTO `${PROJECT}.political_ads_ops.quarantine`
  (run_id, week_start_date, advertiser_id, reason, source_row_hash, recorded_at)
SELECT
  run_id, week_start_date, advertiser_id, validation_reason,
  source_row_hash, CURRENT_TIMESTAMP()
FROM candidate
WHERE validation_reason IS NOT NULL;

ASSERT (SELECT COUNT(*) FROM candidate)
  = (SELECT COUNT(*) FROM candidate WHERE validation_reason IS NULL)
    + (SELECT COUNT(*) FROM `${PROJECT}.political_ads_ops.quarantine`
       WHERE run_id = @run_id)
  AS 'Raw does not reconcile with valid plus quarantine';

ASSERT (SELECT COUNT(*) FROM `${PROJECT}.political_ads_ops.quarantine`
        WHERE run_id = @run_id) = 0
  AS 'Quality gate failed: inspect ops.quarantine';

ASSERT (SELECT COUNT(*) FROM candidate) > 0
  AS 'Empty weekly snapshot requires investigation';

CREATE TEMP TABLE valid AS
SELECT
  advertiser_id, week_start_date, advertiser_name, amount_usd,
  currency_code, source_row_hash, run_id, source_modified_utc
FROM candidate
WHERE validation_reason IS NULL;

-- A source correction replaces the entire observed week, not only matched keys
CREATE TEMP TABLE next_current AS
SELECT
  advertiser_id, week_start_date, advertiser_name, amount_usd,
  currency_code, source_row_hash, run_id, source_modified_utc
FROM `${PROJECT}.political_ads_silver.weekly_current`
WHERE week_start_date != @week_start
UNION ALL
SELECT * FROM valid;

ASSERT (SELECT COUNT(*) FROM next_current)
  = (SELECT COUNT(*) FROM (
       SELECT advertiser_id, week_start_date
       FROM next_current
       GROUP BY advertiser_id, week_start_date
     ))
  AS 'Silver current key is not unique';

CREATE TEMP TABLE next_dim_advertiser AS
SELECT
  advertiser_id,
  COALESCE(NULLIF(TRIM(advertiser_name), ''), advertiser_id)
    AS advertiser_name,
  week_start_date AS last_observed_week
FROM next_current
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY advertiser_id
  ORDER BY source_modified_utc DESC, week_start_date DESC, run_id DESC
) = 1;

CREATE TEMP TABLE next_dim_week AS
SELECT DISTINCT
  week_start_date,
  DATE_ADD(week_start_date, INTERVAL 6 DAY) AS week_end_date,
  EXTRACT(YEAR FROM week_start_date) AS calendar_year,
  EXTRACT(MONTH FROM week_start_date) AS calendar_month
FROM next_current;

CREATE TEMP TABLE next_fact AS
SELECT
  TO_HEX(MD5(CONCAT(advertiser_id, '|', CAST(week_start_date AS STRING))))
    AS advertiser_week_key,
  advertiser_id, week_start_date, amount_usd, currency_code,
  run_id, source_row_hash
FROM next_current;

CREATE TEMP TABLE next_trend AS
WITH weekly AS (
  SELECT
    week_start_date,
    SUM(amount_usd) AS total_spend_usd,
    COUNT(*) AS advertiser_count
  FROM next_fact
  GROUP BY week_start_date
), lagged AS (
  SELECT
    *,
    LAG(week_start_date) OVER (ORDER BY week_start_date) AS previous_week,
    LAG(total_spend_usd) OVER (ORDER BY week_start_date)
      AS previous_spend_usd
  FROM weekly
)
SELECT
  week_start_date, total_spend_usd, advertiser_count,
  previous_spend_usd,
  CASE
    WHEN DATE_DIFF(week_start_date, previous_week, DAY) = 7
      AND previous_spend_usd > 0
      THEN ROUND(100 * SAFE_DIVIDE(
        total_spend_usd - previous_spend_usd, previous_spend_usd
      ), 2)
    ELSE NULL
  END AS wow_change_pct
FROM lagged;

CREATE TEMP TABLE next_advertiser_summary AS
WITH totals AS (
  SELECT
    advertiser_id,
    MIN(week_start_date) AS first_observed_week,
    MAX(week_start_date) AS last_observed_week,
    COUNT(*) AS weeks_observed,
    SUM(amount_usd) AS total_spend_usd,
    ROUND(AVG(amount_usd), 2) AS avg_observed_week_spend_usd
  FROM next_fact
  GROUP BY advertiser_id
), overall AS (
  SELECT SUM(amount_usd) AS total_spend_usd FROM next_fact
)
SELECT
  t.advertiser_id, d.advertiser_name,
  t.first_observed_week, t.last_observed_week,
  t.weeks_observed, t.total_spend_usd,
  t.avg_observed_week_spend_usd,
  ROUND(100 * SAFE_DIVIDE(t.total_spend_usd, o.total_spend_usd), 4)
    AS share_of_observed_spend_pct
FROM totals AS t
JOIN next_dim_advertiser AS d USING (advertiser_id)
CROSS JOIN overall AS o;

CREATE TEMP TABLE next_bi_weekly AS
SELECT
  t.week_start_date, w.week_end_date, w.calendar_year, w.calendar_month,
  t.total_spend_usd, t.advertiser_count,
  t.previous_spend_usd, t.wow_change_pct
FROM next_trend AS t
JOIN next_dim_week AS w USING (week_start_date);

CREATE TEMP TABLE next_api_advertiser AS
SELECT
  advertiser_id, advertiser_name, total_spend_usd,
  weeks_observed, last_observed_week,
  share_of_observed_spend_pct,
  ROW_NUMBER() OVER (
    ORDER BY total_spend_usd DESC, advertiser_id
  ) AS spend_rank
FROM next_advertiser_summary;

CREATE TEMP TABLE next_targeting_coverage AS
SELECT
  'creative_stats' AS required_source,
  'NOT_INGESTED' AS data_status,
  FALSE AS targeting_metrics_ready,
  DATE '2026-10-17' AS source_checked_on,
  'Silver contains only advertiser_weekly_spend' AS reason,
  MAX(week_start_date) AS latest_loaded_week
FROM next_fact
UNION ALL
SELECT
  'campaign_targeting', 'SOURCE_EMPTY_AT_LAST_CHECK', FALSE,
  DATE '2026-10-17', 'Source was empty at week 1 inventory',
  MAX(week_start_date)
FROM next_fact;

ASSERT (SELECT COUNT(*) FROM next_current) = (SELECT COUNT(*) FROM next_fact)
  AS 'Silver and fact row counts differ';
ASSERT (SELECT SUM(amount_usd) FROM next_current)
  = (SELECT SUM(amount_usd) FROM next_fact)
  AS 'Silver and fact spend differ';
ASSERT (SELECT SUM(amount_usd) FROM next_fact)
  = (SELECT SUM(total_spend_usd) FROM next_trend)
  AS 'Trend spend differs from fact';
ASSERT (SELECT SUM(amount_usd) FROM next_fact)
  = (SELECT SUM(total_spend_usd) FROM next_advertiser_summary)
  AS 'Advertiser spend differs from fact';
ASSERT (SELECT SUM(amount_usd) FROM next_fact)
  = (SELECT SUM(total_spend_usd) FROM next_bi_weekly)
  AS 'BI spend differs from fact';

-- Permanent tables must exist before BEGIN TRANSACTION; temp tables are candidates
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.dim_advertiser`
  AS SELECT * FROM next_dim_advertiser WHERE FALSE;
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.dim_week`
  AS SELECT * FROM next_dim_week WHERE FALSE;
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.fct_advertiser_weekly_spend`
  AS SELECT * FROM next_fact WHERE FALSE;
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.mart_spend_trend_weekly`
  AS SELECT * FROM next_trend WHERE FALSE;
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.mart_advertiser_summary`
  AS SELECT * FROM next_advertiser_summary WHERE FALSE;
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.mart_bi_weekly`
  AS SELECT * FROM next_bi_weekly WHERE FALSE;
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.mart_api_advertiser`
  AS SELECT * FROM next_api_advertiser WHERE FALSE;
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_gold.mart_targeting_coverage`
  AS SELECT * FROM next_targeting_coverage WHERE FALSE;

BEGIN TRANSACTION;
  DELETE FROM `${PROJECT}.political_ads_silver.weekly_current` WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_silver.weekly_current`
    SELECT * FROM next_current;

  INSERT INTO `${PROJECT}.political_ads_silver.weekly_history`
  SELECT * FROM valid AS v
  WHERE NOT EXISTS (
    SELECT 1 FROM `${PROJECT}.political_ads_silver.weekly_history` AS h
    WHERE h.run_id = v.run_id
      AND h.advertiser_id = v.advertiser_id
      AND h.week_start_date = v.week_start_date
  );

  DELETE FROM `${PROJECT}.political_ads_gold.dim_advertiser` WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.dim_advertiser`
    SELECT * FROM next_dim_advertiser;
  DELETE FROM `${PROJECT}.political_ads_gold.dim_week` WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.dim_week`
    SELECT * FROM next_dim_week;
  DELETE FROM `${PROJECT}.political_ads_gold.fct_advertiser_weekly_spend`
    WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.fct_advertiser_weekly_spend`
    SELECT * FROM next_fact;
  DELETE FROM `${PROJECT}.political_ads_gold.mart_spend_trend_weekly`
    WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.mart_spend_trend_weekly`
    SELECT * FROM next_trend;
  DELETE FROM `${PROJECT}.political_ads_gold.mart_advertiser_summary`
    WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.mart_advertiser_summary`
    SELECT * FROM next_advertiser_summary;
  DELETE FROM `${PROJECT}.political_ads_gold.mart_bi_weekly` WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.mart_bi_weekly`
    SELECT * FROM next_bi_weekly;
  DELETE FROM `${PROJECT}.political_ads_gold.mart_api_advertiser` WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.mart_api_advertiser`
    SELECT * FROM next_api_advertiser;
  DELETE FROM `${PROJECT}.political_ads_gold.mart_targeting_coverage`
    WHERE TRUE;
  INSERT INTO `${PROJECT}.political_ads_gold.mart_targeting_coverage`
    SELECT * FROM next_targeting_coverage;

  MERGE `${PROJECT}.political_ads_ops.pipeline_runs` AS target
  USING (SELECT @run_id AS run_id) AS source
  ON target.run_id = source.run_id
  WHEN MATCHED THEN UPDATE SET
    status = 'PASS', error_message = NULL,
    updated_at = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (
    run_id, week_start_date, source_modified_utc, parquet_sha256,
    row_count, status, error_message, updated_at
  ) VALUES (
    @run_id, @week_start, @source_modified, @parquet_sha256,
    @expected_rows, 'PASS', NULL, CURRENT_TIMESTAMP()
  );
COMMIT TRANSACTION;

SELECT
  (SELECT COUNT(*) FROM `${PROJECT}.political_ads_silver.weekly_current`)
    AS silver_current_rows,
  (SELECT COUNT(*) FROM `${PROJECT}.political_ads_gold.fct_advertiser_weekly_spend`)
    AS gold_fact_rows,
  (SELECT SUM(amount_usd)
   FROM `${PROJECT}.political_ads_gold.fct_advertiser_weekly_spend`)
    AS gold_total_spend_usd;
