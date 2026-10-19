-- Terraform provisions datasets; this job owns only the tables
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_silver.weekly_current` (
  advertiser_id STRING NOT NULL,
  week_start_date DATE NOT NULL,
  advertiser_name STRING,
  amount_usd NUMERIC NOT NULL,
  currency_code STRING NOT NULL,
  source_row_hash STRING NOT NULL,
  run_id STRING NOT NULL,
  source_modified_utc TIMESTAMP NOT NULL
)
PARTITION BY week_start_date
CLUSTER BY advertiser_id;

-- Observation history, not an SCD2 valid-from/valid-to table
CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_silver.weekly_history` (
  advertiser_id STRING NOT NULL,
  week_start_date DATE NOT NULL,
  advertiser_name STRING,
  amount_usd NUMERIC NOT NULL,
  currency_code STRING NOT NULL,
  source_row_hash STRING NOT NULL,
  run_id STRING NOT NULL,
  source_modified_utc TIMESTAMP NOT NULL
)
PARTITION BY week_start_date
CLUSTER BY advertiser_id, run_id;

CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_ops.quarantine` (
  run_id STRING NOT NULL,
  week_start_date DATE,
  advertiser_id STRING,
  reason STRING NOT NULL,
  source_row_hash STRING NOT NULL,
  recorded_at TIMESTAMP NOT NULL
)
CLUSTER BY run_id;

CREATE TABLE IF NOT EXISTS `${PROJECT}.political_ads_ops.pipeline_runs` (
  run_id STRING NOT NULL,
  week_start_date DATE NOT NULL,
  source_modified_utc TIMESTAMP NOT NULL,
  parquet_sha256 STRING NOT NULL,
  row_count INT64 NOT NULL,
  status STRING NOT NULL,
  error_message STRING,
  updated_at TIMESTAMP NOT NULL
)
CLUSTER BY run_id, week_start_date;
