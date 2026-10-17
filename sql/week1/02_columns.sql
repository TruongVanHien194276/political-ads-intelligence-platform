SELECT
  table_name,
  ordinal_position,
  column_name,
  data_type,
  is_nullable
FROM `bigquery-public-data.google_political_ads.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name IN (
  'creative_stats',
  'advertiser_stats',
  'advertiser_weekly_spend'
)
ORDER BY table_name, ordinal_position