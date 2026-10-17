WITH advertiser_history AS (
  SELECT
    advertiser_id,
    COUNT(DISTINCT SAFE_CAST(week_start_date AS DATE)) AS observed_weeks,
    DATE_DIFF(
      MAX(SAFE_CAST(week_start_date AS DATE)),
      MIN(SAFE_CAST(week_start_date AS DATE)),
      WEEK
    ) + 1 AS calendar_weeks
  FROM `bigquery-public-data.google_political_ads.advertiser_weekly_spend`
  WHERE advertiser_id IS NOT NULL
    AND SAFE_CAST(week_start_date AS DATE) IS NOT NULL
  GROUP BY advertiser_id
)

SELECT
  COUNT(*) AS advertisers_total,
  COUNTIF(observed_weeks >= 26) AS advertisers_26_weeks,
  COUNTIF(observed_weeks >= 52) AS advertisers_52_weeks,
  COUNTIF(
    observed_weeks >= 52
    AND calendar_weeks >= 78
  ) AS advertisers_long_history,
  APPROX_QUANTILES(observed_weeks, 4) AS observed_weeks_quartiles
FROM advertiser_history