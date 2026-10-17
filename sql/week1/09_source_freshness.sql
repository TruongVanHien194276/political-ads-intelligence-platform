SELECT
  report_data_updated_time,
  DATETIME_DIFF(CURRENT_DATETIME('UTC'), report_data_updated_time, HOUR)
    AS hours_since_update
FROM `bigquery-public-data.google_political_ads.last_updated`
