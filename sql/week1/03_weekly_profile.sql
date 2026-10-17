SELECT
  COUNT(*) AS rows_total,
  COUNT(DISTINCT advertiser_id) AS advertisers,
  MIN(SAFE_CAST(week_start_date AS DATE)) AS first_week,
  MAX(SAFE_CAST(week_start_date AS DATE)) AS last_week,

  COUNTIF(advertiser_id IS NULL) AS null_advertiser_id,
  COUNTIF(SAFE_CAST(week_start_date AS DATE) IS NULL) AS invalid_week,
  COUNTIF(SAFE_CAST(spend_usd AS NUMERIC) IS NULL) AS invalid_spend,
  COUNTIF(SAFE_CAST(spend_usd AS NUMERIC) < 0) AS negative_spend,
  COUNTIF(SAFE_CAST(spend_usd AS NUMERIC) = 0) AS zero_spend

FROM `bigquery-public-data.google_political_ads.advertiser_weekly_spend`