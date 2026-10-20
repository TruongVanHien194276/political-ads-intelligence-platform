from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from apps.api.main import SQL, Settings, create_app


@pytest.fixture
def api_client(tmp_path: Path):
    gold = tmp_path / "gold.duckdb"
    forecast = tmp_path / "forecast.duckdb"

    with duckdb.connect(str(gold)) as con:
        con.execute("""
            CREATE TABLE mart_bi_weekly AS
            SELECT
                DATE '2026-10-04' AS week_start_date,
                100.0 AS total_spend_usd,
                1 AS advertiser_count,
                NULL::DOUBLE AS wow_change_pct
            UNION ALL
            SELECT DATE '2026-10-11', 300.0, 2, 200.0
        """)

        con.execute("""
            CREATE TABLE mart_api_advertiser AS
            SELECT
                'AR1' AS advertiser_id,
                'Advertiser One' AS advertiser_name,
                250.0 AS total_spend_usd,
                2 AS weeks_observed,
                DATE '2026-10-11' AS last_observed_week,
                62.5 AS share_of_observed_spend_pct,
                1 AS spend_rank
            UNION ALL
            SELECT 'AR2', 'Advertiser Two', 150.0, 1,
                   DATE '2026-10-11', 37.5, 2
        """)

        con.execute("""
            CREATE TABLE fct_advertiser_weekly_spend AS
            SELECT 'AR1' AS advertiser_id,
                   DATE '2026-10-11' AS week_start_date,
                   250.0 AS amount_usd
        """)

        con.execute("CREATE SCHEMA audit")
        con.execute("""
            CREATE TABLE audit.pipeline_runs AS
            SELECT
                'test-run' AS run_id,
                now() AS finished_at_utc,
                'PASS' AS status
        """)
        con.execute("""
            CREATE TABLE audit.dq_scorecard AS
            SELECT
                'test-run' AS run_id,
                'no_negative_spend' AS check_name,
                'PASS' AS status,
                'BLOCK' AS severity
        """)

    with duckdb.connect(str(forecast)) as con:
        con.execute("CREATE SCHEMA serving")
        con.execute("""
            CREATE TABLE serving.forecast_current AS
            SELECT
                'AR1' AS advertiser_id,
                DATE '2026-10-11' AS data_cutoff_week,
                DATE '2026-10-18' AS target_week,
                250.0 AS point,
                200.0 AS p10,
                250.0 AS p50,
                350.0 AS p90,
                '1' AS model_version,
                'mlflow-test-run' AS model_run_id,
                'LIVE' AS run_mode
        """)

    settings = Settings(
        gold_db=gold,
        forecast_db=forecast,
        cors_origins=("http://localhost:5173",),
        cache_seconds=1,
    )

    with TestClient(create_app(settings)) as client:
        yield client, gold


def test_three_overview_kpis_match_sql(api_client):
    client, gold = api_client
    response = client.get("/api/v1/overview")
    assert response.status_code == 200

    payload = response.json()

    with duckdb.connect(str(gold), read_only=True) as con:
        latest_spend, latest_advertisers = con.execute("""
            SELECT total_spend_usd, advertiser_count
            FROM mart_bi_weekly
            ORDER BY week_start_date DESC
            LIMIT 1
        """).fetchone()

        loaded_spend = con.execute("""
            SELECT SUM(total_spend_usd)
            FROM mart_bi_weekly
        """).fetchone()[0]

    assert payload["total_spend_usd"] == latest_spend
    assert payload["advertiser_count"] == latest_advertisers
    assert payload["loaded_total_spend_usd"] == loaded_spend


def test_filters_pagination_and_no_raw_field(api_client):
    client, _ = api_client

    assert client.get("/api/v1/advertisers?sort=unknown").status_code == 422

    assert client.get("/api/v1/advertisers?limit=101").status_code == 422

    page = client.get("/api/v1/advertisers?limit=1").json()

    assert len(page["items"]) == 1
    assert page["has_more"] is True
    assert "source_row_hash" not in str(page)


def test_forecast_and_request_id(api_client):
    client, _ = api_client

    response = client.get("/api/v1/forecasts")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]

    forecast = response.json()["items"][0]
    assert forecast["point"] == 250.0
    assert forecast["actual_usd"] is None


def test_geo_does_not_invent_data(api_client):
    client, _ = api_client
    result = client.get("/api/v1/geography").json()
    assert result["available"] is False


def test_trends_detail_and_health(api_client):
    client, _ = api_client
    trends = client.get("/api/v1/trends?from_week=2026-10-11")
    assert trends.status_code == 200
    assert len(trends.json()) == 1
    assert trends.json()[0]["week_start_date"] == "2026-10-11"

    invalid_range = client.get("/api/v1/trends?from_week=2026-10-12&to_week=2026-10-11")
    assert invalid_range.status_code == 422
    detail = client.get("/api/v1/advertisers/AR1").json()
    assert detail["advertiser_name"] == "Advertiser One"
    assert client.get("/api/v1/advertisers/unknown").status_code == 404

    health = client.get("/api/v1/pipeline-health")
    assert health.status_code == 200
    assert health.json()["blocking_failures"] == 0


def test_cors_request_id_and_openapi(api_client):
    client, _ = api_client
    response = client.get(
        "/api/v1/overview",
        headers={"Origin": "http://localhost:5173", "X-Request-ID": "demo-123"},
    )
    assert response.headers["X-Request-ID"] == "demo-123"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    invalid = client.get("/healthz", headers={"X-Request-ID": "bad id"})
    assert invalid.headers["X-Request-ID"] != "bad id"
    assert "/api/v1/forecasts" in client.get("/openapi.json").json()["paths"]


def test_missing_forecast_returns_503(tmp_path: Path):
    gold = tmp_path / "gold.duckdb"
    gold.touch()
    settings = Settings(gold, tmp_path / "missing.duckdb", ())
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/forecasts")
    assert response.status_code == 503


def test_local_query_timeout(api_client, monkeypatch):
    client, _ = api_client
    client.app.state.store.settings = Settings(
        gold_db=client.app.state.store.settings.gold_db,
        forecast_db=client.app.state.store.settings.forecast_db,
        cors_origins=(),
        query_timeout_seconds=0.01,
    )
    monkeypatch.setitem(
        SQL,
        "trends",
        """SELECT SUM(i) FROM range(1000000000) AS t(i)
           WHERE (@from_week IS NULL OR @to_week IS NULL) AND @limit > 0""",
    )
    response = client.get("/api/v1/trends")
    assert response.status_code == 504
