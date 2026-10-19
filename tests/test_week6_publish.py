from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts import week6_publish as publish


def set_paths(tmp_path, monkeypatch):
    live = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(live)) as con:
        con.execute("CREATE TABLE sentinel (value INTEGER)")
        con.execute("INSERT INTO sentinel VALUES (1)")

    monkeypatch.setattr(publish, "LIVE", live)
    monkeypatch.setattr(publish, "TMP", tmp_path / "week6")
    monkeypatch.setattr(publish, "BACKUPS", tmp_path / "week6" / "backups")
    return live


def test_schema_drift_detected_from_parquet_metadata(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "raw"
    for run_id, columns in (
        ("r1", {"advertiser_id": ["a"]}),
        ("r2", {"advertiser_id": ["a"], "new_column": [1]}),
    ):
        directory = (
            raw
            / "source=google_political_ads"
            / "table=advertiser_weekly_spend"
            / "week_start=2026-10-04"
            / f"run_id={run_id}"
        )
        directory.mkdir(parents=True)
        pq.write_table(pa.table(columns), directory / "data.parquet")

    monkeypatch.setattr(publish, "RAW", raw)
    with pytest.raises(RuntimeError, match="Schema drift"):
        publish.check_raw_schema()


def test_staging_assigns_quarantine_reasons() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA bronze")
    con.execute("""
        CREATE TABLE bronze.weekly_raw (
            run_id VARCHAR,
            row_in_run BIGINT,
            advertiser_id VARCHAR,
            week_start_date VARCHAR,
            snapshot_week_start DATE,
            spend_usd VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO bronze.weekly_raw VALUES (?, ?, ?, ?, DATE '2026-10-04', ?)",
        [
            ("r1", 1, "ok", "2026-10-04", "100"),
            ("r1", 2, None, "2026-10-04", "5"),
            ("r1", 3, "negative", "2026-10-04", "-2"),
            ("r1", 4, "duplicate", "2026-10-04", "10"),
            ("r1", 5, "duplicate", "2026-10-04", "10"),
            ("r1", 6, "bad_date", "not-a-date", "1"),
            ("r1", 7, "monday", "2026-10-05", "1"),
            ("r1", 8, "mismatch", "2026-09-27", "1"),
        ],
    )
    source = Path("transform/models/stg_weekly.sql").read_text(encoding="utf-8")
    sql = source.replace("{{ source('bronze', 'weekly_raw') }}", "bronze.weekly_raw")
    con.execute(f"CREATE VIEW main.stg_weekly AS {sql}")

    assert dict(
        con.execute("""
        SELECT row_in_run, validation_error
        FROM main.stg_weekly
        ORDER BY row_in_run
    """).fetchall()
    ) == {
        1: None,
        2: "missing_advertiser_id",
        3: "negative_spend",
        4: None,
        5: "duplicate_key_in_run",
        6: "invalid_week_start",
        7: "week_not_sunday",
        8: "week_mismatch_manifest",
    }
    con.close()


def test_blocking_quality_failure_keeps_published_data(tmp_path, monkeypatch) -> None:
    live = set_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(publish, "check_raw_schema", lambda: None)
    monkeypatch.setattr(publish, "run_stage", lambda command, candidate: None)
    monkeypatch.setattr(
        publish,
        "evaluate",
        lambda candidate: [
            {
                "name": "negative_spend_zero",
                "status": "FAIL",
                "severity": "BLOCK",
                "observed": "1",
                "expected": "0",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="negative_spend_zero"):
        publish.main()

    with duckdb.connect(str(live), read_only=True) as con:
        assert con.execute("SELECT value FROM sentinel").fetchone() == (1,)
        assert con.execute("SELECT status FROM audit.pipeline_runs").fetchone() == (
            "FAIL",
        )
        assert con.execute("SELECT status FROM audit.dq_scorecard").fetchone() == (
            "FAIL",
        )
    assert not list((tmp_path / "week6").glob("candidate-*"))


def test_success_replaces_database_and_keeps_backup(tmp_path, monkeypatch) -> None:
    live = set_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(publish, "check_raw_schema", lambda: None)
    calls = 0

    def fake_stage(command, candidate):
        nonlocal calls
        if calls == 0:
            with duckdb.connect(str(candidate)) as con:
                con.execute("UPDATE sentinel SET value = 2")
        calls += 1

    monkeypatch.setattr(publish, "run_stage", fake_stage)
    monkeypatch.setattr(
        publish,
        "evaluate",
        lambda candidate: [
            {
                "name": "silver_equals_fact_rows",
                "status": "PASS",
                "severity": "BLOCK",
                "observed": "1",
                "expected": "1",
            }
        ],
    )

    publish.main()

    backups = list((tmp_path / "week6" / "backups").glob("*.duckdb"))
    assert calls == 4
    assert len(backups) == 1
    with duckdb.connect(str(live), read_only=True) as con:
        assert con.execute("SELECT value FROM sentinel").fetchone() == (2,)
        assert con.execute("SELECT status FROM audit.pipeline_runs").fetchone() == (
            "PASS",
        )
    with duckdb.connect(str(backups[0]), read_only=True) as con:
        assert con.execute("SELECT value FROM sentinel").fetchone() == (1,)
