import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
LIVE = ROOT / "data/tmp/week4/warehouse.duckdb"
TMP = ROOT / "data/tmp/week6"
BACKUPS = TMP / "backups"

DBT = ROOT / "data/tmp/week4/venv/bin/dbt"
DBT_PYTHON = ROOT / "data/tmp/week4/venv/bin/python"


def raw_parquets() -> list[Path]:
    return sorted(RAW.glob("source=*/table=*/week_start=*/run_id=*/data.parquet"))


def schema_signature(path: Path) -> tuple[tuple[str, str], ...]:
    # Chỉ đọc metadata Parquet, không kéo toàn bộ dữ liệu vào RAM
    schema = pq.read_schema(path)
    return tuple((field.name, str(field.type)) for field in schema)


def check_raw_schema() -> None:
    paths = raw_parquets()
    if not paths:
        raise RuntimeError("Không tìm thấy raw Parquet")

    expected = schema_signature(paths[0])

    for path in paths[1:]:
        actual = schema_signature(path)
        if actual != expected:
            raise RuntimeError(
                f"Schema drift ở {path}\nExpected: {expected}\nActual: {actual}"
            )

    print(f"SCHEMA_OK snapshots={len(paths)}")


def run_stage(command: list[str], candidate: Path) -> None:
    env = os.environ.copy()
    env["WAREHOUSE_DB_PATH"] = str(candidate)
    env["WEEK4_DB_PATH"] = str(candidate)

    print("RUN", " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def scalar(con: duckdb.DuckDBPyConnection, sql: str):
    return con.execute(sql).fetchone()[0]


def run_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return dict(
        con.execute("""
            SELECT run_id, count(*)
            FROM main.silver_history
            GROUP BY run_id
        """).fetchall()
    )


def loaded_runs(con: duckdb.DuckDBPyConnection) -> dict:
    return {
        run_id: (row_count, checksum)
        for run_id, row_count, checksum in con.execute("""
            SELECT run_id, row_count, parquet_sha256
            FROM bronze.loaded_runs
        """).fetchall()
    }


def evaluate(candidate: Path) -> list[dict]:
    with duckdb.connect(str(LIVE), read_only=True) as old:
        old_history = run_counts(old)
        old_runs = loaded_runs(old)

    checks = []

    def add(name, observed, expected, severity="BLOCK"):
        ok = observed == expected
        checks.append(
            {
                "name": name,
                "status": "PASS" if ok else ("WARN" if severity == "WARN" else "FAIL"),
                "severity": severity,
                "observed": str(observed),
                "expected": str(expected),
            }
        )

    with duckdb.connect(str(candidate), read_only=True) as con:
        bronze = scalar(con, "SELECT count(*) FROM bronze.weekly_raw")
        valid = scalar(con, "SELECT count(*) FROM main.valid_weekly")
        quarantine = scalar(con, "SELECT count(*) FROM main.quarantine_weekly")
        silver = scalar(con, "SELECT count(*) FROM silver.weekly_current")
        fact = scalar(
            con,
            "SELECT count(*) FROM main.fct_advertiser_weekly_spend",
        )

        latest_raw = scalar(
            con,
            """
            SELECT coalesce(sum(row_count), 0)
            FROM (
                SELECT row_count
                FROM bronze.loaded_runs
                QUALIFY row_number() OVER (
                    PARTITION BY snapshot_week_start
                    ORDER BY source_modified_utc DESC, run_id DESC
                ) = 1
            )
        """,
        )

        latest_quarantine = scalar(
            con,
            """
            WITH latest AS (
                SELECT run_id
                FROM bronze.loaded_runs
                QUALIFY row_number() OVER (
                    PARTITION BY snapshot_week_start
                    ORDER BY source_modified_utc DESC, run_id DESC
                ) = 1
            )
            SELECT count(*)
            FROM main.quarantine_weekly AS q
            JOIN latest USING (run_id)
        """,
        )

        duplicate_current = scalar(
            con,
            """
            SELECT count(*) - count(
                DISTINCT advertiser_id || '|'
                || cast(week_start_date AS varchar)
            )
            FROM silver.weekly_current
        """,
        )

        negative_spend = scalar(
            con,
            """
            SELECT count(*)
            FROM silver.weekly_current
            WHERE amount_usd < 0
        """,
        )

        # Cùng timestamp nguồn nhưng hai nội dung khác nhau là lựa chọn
        # latest run mơ hồ; không publish
        ambiguous_versions = scalar(
            con,
            """
            SELECT count(*)
            FROM (
                SELECT snapshot_week_start, source_modified_utc
                FROM bronze.loaded_runs
                GROUP BY 1, 2
                HAVING count(DISTINCT parquet_sha256) > 1
            )
        """,
        )

        fact_spend = scalar(
            con,
            """
            SELECT sum(amount_usd)
            FROM main.fct_advertiser_weekly_spend
        """,
        )

        add("raw_equals_valid_plus_quarantine", bronze, valid + quarantine)
        add("latest_raw_equals_silver", latest_raw, silver)
        add("latest_quarantine_zero", latest_quarantine, 0)
        add("silver_key_duplicate_zero", duplicate_current, 0)
        add("negative_spend_zero", negative_spend, 0)
        add("ambiguous_versions_zero", ambiguous_versions, 0)
        add("silver_equals_fact_rows", silver, fact)
        add("nonempty_fact", fact > 0, True)

        for name, query in {
            "silver_spend": ("SELECT sum(amount_usd) FROM silver.weekly_current"),
            "weekly_mart_spend": (
                "SELECT sum(total_spend_usd) FROM main.mart_spend_trend_weekly"
            ),
            "advertiser_mart_spend": (
                "SELECT sum(total_spend_usd) FROM main.mart_advertiser_summary"
            ),
            "bi_mart_spend": ("SELECT sum(total_spend_usd) FROM main.mart_bi_weekly"),
            "api_mart_spend": (
                "SELECT sum(total_spend_usd) FROM main.mart_api_advertiser"
            ),
        }.items():
            add(name, scalar(con, query), fact_spend)

        new_history = run_counts(con)
        new_runs = loaded_runs(con)

        # Snapshot cũ phải còn nguyên trong Bronze và Silver history
        add(
            "old_raw_runs_preserved",
            all(new_runs.get(k) == v for k, v in old_runs.items()),
            True,
        )
        add(
            "old_history_preserved",
            all(new_history.get(k, 0) >= count for k, count in old_history.items()),
            True,
        )

        latest_week = scalar(
            con,
            """
            SELECT max(snapshot_week_start)
            FROM bronze.loaded_runs
        """,
        )
        lag_days = (date.today() - latest_week).days

        # Freshness là cảnh báo: backfill lịch sử không được fail
        # chỉ vì tuần cuối cùng đã cũ
        add("latest_week_lag_days", lag_days, 14, "WARN")
        checks[-1]["status"] = "PASS" if lag_days <= 14 else "WARN"
        checks[-1]["expected"] = "<=14"

    return checks


def quarantine_reasons(candidate: Path) -> str:
    try:
        with duckdb.connect(str(candidate), read_only=True) as con:
            rows = con.execute("""
                SELECT validation_error, count(*)
                FROM main.quarantine_weekly
                GROUP BY validation_error
                ORDER BY count(*) DESC
            """).fetchall()
        return json.dumps(dict(rows), ensure_ascii=False)
    except duckdb.Error:
        return "unavailable"


def write_audit(
    db_path: Path,
    run_id: str,
    status: str,
    message: str,
    checks: list[dict],
) -> None:
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS audit")
        con.execute("""
            CREATE TABLE IF NOT EXISTS audit.pipeline_runs (
                run_id VARCHAR PRIMARY KEY,
                finished_at_utc TIMESTAMPTZ,
                status VARCHAR,
                message VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS audit.dq_scorecard (
                run_id VARCHAR,
                check_name VARCHAR,
                status VARCHAR,
                severity VARCHAR,
                observed VARCHAR,
                expected VARCHAR
            )
        """)

        con.execute(
            "INSERT INTO audit.pipeline_runs VALUES (?, ?, ?, ?)",
            [run_id, datetime.now(timezone.utc), status, message[:2000]],
        )

        for check in checks:
            con.execute(
                "INSERT INTO audit.dq_scorecard VALUES (?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    check["name"],
                    check["status"],
                    check["severity"],
                    check["observed"],
                    check["expected"],
                ],
            )

        con.execute("CHECKPOINT")


def main() -> None:
    if not LIVE.is_file():
        raise RuntimeError("Chạy Tuần 4/5 trước khi chạy Tuần 6")

    TMP.mkdir(parents=True, exist_ok=True)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    checks = []

    # Không chạy đồng thời dbt, Power BI connector hay script khác
    # đang mở warehouse trong chế độ ghi
    with duckdb.connect(str(LIVE)) as con:
        con.execute("CHECKPOINT")

    with tempfile.TemporaryDirectory(prefix="candidate-", dir=TMP) as directory:
        candidate = Path(directory) / "warehouse.duckdb"
        shutil.copy2(LIVE, candidate)

        try:
            check_raw_schema()

            run_stage(
                [str(DBT_PYTHON), "scripts/week4_load.py"],
                candidate,
            )
            run_stage(
                [
                    str(DBT),
                    "build",
                    "--project-dir",
                    "transform",
                    "--profiles-dir",
                    "transform",
                    "--exclude",
                    "path:models/gold",
                ],
                candidate,
            )
            run_stage(
                [str(DBT_PYTHON), "scripts/week4_promote.py"],
                candidate,
            )
            run_stage(
                [
                    str(DBT),
                    "build",
                    "--project-dir",
                    "transform",
                    "--profiles-dir",
                    "transform",
                    "--select",
                    "tag:gold",
                ],
                candidate,
            )

            checks = evaluate(candidate)
            failures = [
                item["name"]
                for item in checks
                if item["status"] == "FAIL" and item["severity"] == "BLOCK"
            ]
            if failures:
                raise RuntimeError("Quality gate fail: " + ", ".join(failures))

            write_audit(
                candidate,
                run_id,
                "PASS",
                "Gold được publish sau dbt và DQ checks",
                checks,
            )

            # Backup có thể khôi phục; candidate cùng filesystem
            # nên replace file là atomic khi mọi connection đã đóng
            backup = BACKUPS / f"{run_id}.duckdb"
            shutil.copy2(LIVE, backup)
            os.replace(candidate, LIVE)

            print(f"PUBLISHED run_id={run_id}")
            print(f"BACKUP={backup}")

        except Exception as exc:
            reason = quarantine_reasons(candidate)
            write_audit(
                LIVE,
                run_id,
                "FAIL",
                f"{exc}; quarantine_reasons={reason}",
                checks,
            )
            print(f"NOT_PUBLISHED run_id={run_id}")
            raise


if __name__ == "__main__":
    main()
