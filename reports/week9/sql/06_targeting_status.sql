SELECT
            required_source,
            data_status,
            targeting_metrics_ready,
            source_checked_on,
            latest_loaded_week,
            reason
        FROM mart_targeting_coverage
        ORDER BY required_source
