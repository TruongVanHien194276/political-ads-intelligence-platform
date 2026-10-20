CREATE SCHEMA IF NOT EXISTS serving;

CREATE TABLE IF NOT EXISTS serving.forecast_runs (
    run_id VARCHAR PRIMARY KEY,
    data_cutoff_week DATE NOT NULL,
    target_week DATE NOT NULL,
    model_name VARCHAR NOT NULL,
    model_version VARCHAR NOT NULL,
    model_run_id VARCHAR NOT NULL,
    source_fingerprint VARCHAR NOT NULL,
    cohort_size INTEGER NOT NULL,
    scored_count INTEGER NOT NULL,
    abstained_count INTEGER NOT NULL,
    run_mode VARCHAR NOT NULL,
    created_at_utc TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS serving.forecast_history (
    forecast_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    advertiser_id VARCHAR NOT NULL,
    data_cutoff_week DATE NOT NULL,
    target_week DATE NOT NULL,
    model_name VARCHAR NOT NULL,
    model_version VARCHAR NOT NULL,
    model_run_id VARCHAR NOT NULL,
    source_fingerprint VARCHAR NOT NULL,
    lag_1 DOUBLE NOT NULL,
    point DOUBLE NOT NULL,
    p10 DOUBLE NOT NULL,
    p50 DOUBLE NOT NULL,
    p90 DOUBLE NOT NULL,
    run_mode VARCHAR NOT NULL,
    created_at_utc TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS serving.forecast_evaluations (
    forecast_id VARCHAR NOT NULL,
    actual_source_row_hash VARCHAR NOT NULL,
    actual DOUBLE NOT NULL,
    absolute_error DOUBLE NOT NULL,
    covered_80 BOOLEAN NOT NULL,
    spike BOOLEAN NOT NULL,
    evaluated_at_utc TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (forecast_id, actual_source_row_hash)
);

CREATE TABLE IF NOT EXISTS serving.drift_checks (
    run_id VARCHAR PRIMARY KEY,
    input_psi DOUBLE,
    input_rows INTEGER NOT NULL,
    reference_rows INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    checked_at_utc TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS serving.forecast_alerts (
    alert_id VARCHAR PRIMARY KEY,
    forecast_id VARCHAR NOT NULL,
    actual_source_row_hash VARCHAR NOT NULL,
    alert_type VARCHAR NOT NULL,
    actual DOUBLE NOT NULL,
    point DOUBLE NOT NULL,
    p90 DOUBLE NOT NULL,
    created_at_utc TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE VIEW serving.forecast_current AS
SELECT * EXCLUDE (rn)
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY advertiser_id, target_week, run_mode
        ORDER BY data_cutoff_week DESC, created_at_utc DESC, forecast_id DESC
    ) AS rn
    FROM serving.forecast_history
)
WHERE rn = 1;
