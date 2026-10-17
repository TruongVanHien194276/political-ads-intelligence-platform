SELECT
  COUNT(*) AS rows_total,
  COUNTIF(spend_usd IS NOT NULL) AS rows_with_usd,
  COUNTIF(spend_usd > 0) AS rows_positive_usd,
  COUNTIF(spend_eur > 0) AS rows_positive_eur,
  COUNTIF(spend_gbp > 0) AS rows_positive_gbp,
  COUNTIF(spend_usd > 0 AND spend_eur > 0) AS rows_positive_usd_and_eur,
  COUNTIF(election_cycle IS NULL) AS null_election_cycle,
  COUNT(DISTINCT election_cycle) AS election_cycles
FROM `bigquery-public-data.google_political_ads.advertiser_weekly_spend`
