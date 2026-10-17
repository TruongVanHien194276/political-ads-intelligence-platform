SELECT
  COUNT(*) AS creative_rows,
  COUNT(DISTINCT ad_id) AS distinct_ad_ids,
  COUNTIF(ad_id IS NULL) AS null_ad_id,
  COUNTIF(advertiser_id IS NULL) AS null_advertiser_id,
  MIN(SAFE_CAST(date_range_start AS DATE)) AS first_start_date,
  MAX(SAFE_CAST(date_range_end AS DATE)) AS last_end_date,
  COUNTIF(spend_range_min_usd IS NULL) AS null_spend_min,
  COUNTIF(spend_range_max_usd IS NULL) AS null_spend_max,
  COUNTIF(
    SAFE_CAST(spend_range_min_usd AS NUMERIC)
    >
    SAFE_CAST(spend_range_max_usd AS NUMERIC)
  ) AS reversed_spend_band
FROM `bigquery-public-data.google_political_ads.creative_stats`