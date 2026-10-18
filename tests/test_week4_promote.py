import duckdb
import pytest

from scripts.week4_promote import promote


def fixture_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA bronze")
    con.execute("CREATE TABLE bronze.weekly_raw (run_id VARCHAR)")
    con.execute("CREATE TABLE main.quarantine_weekly (run_id VARCHAR)")
    con.execute("""
        CREATE TABLE bronze.loaded_runs (
            snapshot_week_start DATE,
            run_id VARCHAR,
            source_modified_utc TIMESTAMPTZ
        )
    """)
    con.execute("""
        CREATE TABLE main.valid_weekly (
            run_id VARCHAR,
            advertiser_id_norm VARCHAR,
            week_start_norm DATE,
            advertiser_name VARCHAR,
            amount_usd DECIMAL(18, 2),
            currency_code VARCHAR,
            source_row_hash VARCHAR,
            source_modified_utc TIMESTAMPTZ
        )
    """)
    return con


def add_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    modified: str,
    rows: list[tuple[str, int]],
) -> None:
    con.execute(
        "INSERT INTO bronze.loaded_runs VALUES (DATE '2026-10-04', ?, ?)",
        [run_id, modified],
    )
    for advertiser_id, amount in rows:
        con.execute("INSERT INTO bronze.weekly_raw VALUES (?)", [run_id])
        con.execute(
            """
            INSERT INTO main.valid_weekly VALUES (
                ?, ?, DATE '2026-10-04', ?, ?, 'USD', ?, ?
            )
        """,
            [run_id, advertiser_id, advertiser_id, amount, f"hash-{amount}", modified],
        )


def test_replay_and_corrected_snapshot_keep_current_unique() -> None:
    con = fixture_db()
    add_run(con, "r1", "2026-10-18 00:00:00+00", [("a", 100), ("b", 200)])

    assert promote(con) == 2
    assert promote(con) == 2  # Replay cùng run không nhân đôi

    # Snapshot mới sửa a, xóa b và thêm c
    add_run(con, "r2", "2026-10-19 00:00:00+00", [("a", 150), ("c", 300)])
    assert promote(con) == 2
    assert con.execute("""
        SELECT advertiser_id, amount_usd, run_id
        FROM silver.weekly_current ORDER BY advertiser_id
    """).fetchall() == [("a", 150, "r2"), ("c", 300, "r2")]
    assert promote(con) == 2


def test_quarantine_blocks_promotion() -> None:
    con = fixture_db()
    add_run(con, "r1", "2026-10-18 00:00:00+00", [("a", 100)])
    con.execute("INSERT INTO main.quarantine_weekly VALUES ('r1')")

    with pytest.raises(RuntimeError, match="Quality gate fail"):
        promote(con)
    assert con.execute("SELECT count(*) FROM silver.weekly_current").fetchone()[0] == 0
