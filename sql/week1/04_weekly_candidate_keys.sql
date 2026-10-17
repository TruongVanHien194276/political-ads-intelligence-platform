SELECT
  advertiser_id,
  SAFE_CAST(week_start_date AS DATE) AS week_start,
  COUNT(*) AS row_count
FROM `bigquery-public-data.google_political_ads.advertiser_weekly_spend`
GROUP BY 1, 2
HAVING COUNT(*) > 1
ORDER BY row_count DESC
LIMIT 20