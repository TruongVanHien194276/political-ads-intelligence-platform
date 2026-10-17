WITH geo AS (
  SELECT
    advertiser_id,
    SUM(IF(country = 'US', COALESCE(spend_usd, 0), 0)) AS us_spend_usd,
    SUM(IF(country != 'US', COALESCE(spend_usd, 0), 0)) AS other_spend_usd
  FROM `bigquery-public-data.google_political_ads.advertiser_geo_spend`
  GROUP BY advertiser_id
),
weekly AS (
  SELECT
    advertiser_id,
    COUNTIF(spend_usd > 0) AS positive_usd_weeks
  FROM `bigquery-public-data.google_political_ads.advertiser_weekly_spend`
  GROUP BY advertiser_id
)
SELECT
  COUNT(*) AS advertisers_with_us_spend,
  COUNTIF(other_spend_usd = 0) AS advertisers_us_only_in_geo,
  COUNTIF(positive_usd_weeks >= 26) AS advertisers_26_positive_usd_weeks,
  COUNTIF(positive_usd_weeks >= 52) AS advertisers_52_positive_usd_weeks,
  COUNTIF(other_spend_usd = 0 AND positive_usd_weeks >= 52)
    AS us_only_advertisers_52_positive_usd_weeks
FROM geo
JOIN weekly USING (advertiser_id)
WHERE us_spend_usd > 0
