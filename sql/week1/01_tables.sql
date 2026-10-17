SELECT
  table_name,
  table_type
FROM `bigquery-public-data.google_political_ads.INFORMATION_SCHEMA.TABLES`
ORDER BY table_name
