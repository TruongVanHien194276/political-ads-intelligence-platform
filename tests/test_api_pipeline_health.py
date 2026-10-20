from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app


def test_pipeline_health_reads_timestamp_without_pytz(tmp_path: Path):
    gold = tmp_path / "gold.duckdb"
    with duckdb.connect(str(gold)) as con:
        con.execute("CREATE SCHEMA audit")
        con.execute("""
            CREATE TABLE audit.pipeline_runs AS
            SELECT 'run-1' AS run_id,
                   TIMESTAMPTZ '2026-10-20 05:18:48+00' AS finished_at_utc,
                   'PASS' AS status
        """)
        con.execute("""
            CREATE TABLE audit.dq_scorecard AS
            SELECT 'run-1' AS run_id, 'PASS' AS status,
                   'BLOCK' AS severity
        """)
        con.execute("""
            CREATE TABLE fct_advertiser_weekly_spend AS
            SELECT DATE '2026-10-11' AS week_start_date
        """)

    settings = Settings(
        gold_db=gold,
        forecast_db=tmp_path / "unused.duckdb",
        cors_origins=("http://localhost:5173",),
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/pipeline-health")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-1",
        "finished_at_utc": "2026-10-20T05:18:48Z",
        "status": "PASS",
        "blocking_failures": 0,
        "gold_latest_week": "2026-10-11",
    }
