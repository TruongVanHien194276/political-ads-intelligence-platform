SELECT
  country,
  COUNT(*) AS rows_total,
  COUNT(DISTINCT advertiser_id) AS advertisers,
  COUNTIF(spend_usd > 0) AS rows_positive_usd,
  SUM(COALESCE(spend_usd, 0)) AS spend_usd
FROM `bigquery-public-data.google_political_ads.advertiser_geo_spend`
GROUP BY country
ORDER BY spend_usd DESC
LIMIT 20
