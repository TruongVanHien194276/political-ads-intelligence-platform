import os
from pathlib import Path

import duckdb

DB = Path(
    os.getenv(
        "WAREHOUSE_DB_PATH",
        "data/tmp/week4/warehouse.duckdb",
    )
)


def count_for_run(con, table: str, run_id: str) -> int:
    return con.execute(
        f"SELECT count(*) FROM {table} WHERE run_id = ?",
        [run_id],
    ).fetchone()[0]


def promote(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 2")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")

    con.execute("""
        CREATE TABLE IF NOT EXISTS silver.weekly_current (
            advertiser_id VARCHAR NOT NULL,
            week_start_date DATE NOT NULL,
            advertiser_name VARCHAR,
            amount_usd DECIMAL(18, 2) NOT NULL,
            currency_code VARCHAR NOT NULL,
            source_row_hash VARCHAR NOT NULL,
            run_id VARCHAR NOT NULL,
            source_modified_utc TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (advertiser_id, week_start_date)
        )
    """)

    # Mỗi tuần chỉ promote run mới nhất
    runs = con.execute("""
        SELECT snapshot_week_start, run_id
        FROM bronze.loaded_runs
        QUALIFY row_number() OVER (
            PARTITION BY snapshot_week_start
            ORDER BY source_modified_utc DESC, run_id DESC
        ) = 1
        ORDER BY snapshot_week_start
    """).fetchall()

    for week_start, run_id in runs:
        raw = count_for_run(con, "bronze.weekly_raw", run_id)
        valid = count_for_run(con, "main.valid_weekly", run_id)
        bad = count_for_run(con, "main.quarantine_weekly", run_id)

        # Không publish một snapshot lỗi hoặc mất dòng
        if raw != valid + bad or bad != 0:
            raise RuntimeError(
                f"Quality gate fail {run_id}: raw={raw}, valid={valid}, bad={bad}"
            )

        try:
            con.execute("BEGIN TRANSACTION")

            con.execute(
                """
                MERGE INTO silver.weekly_current AS target
                USING (
                    SELECT
                        advertiser_id_norm AS advertiser_id,
                        week_start_norm AS week_start_date,
                        advertiser_name,
                        amount_usd,
                        currency_code,
                        source_row_hash,
                        run_id,
                        source_modified_utc
                    FROM main.valid_weekly
                    WHERE run_id = ?
                ) AS source
                ON target.advertiser_id = source.advertiser_id
                   AND target.week_start_date = source.week_start_date

                WHEN MATCHED AND (
                    target.source_row_hash <> source.source_row_hash
                    OR target.run_id <> source.run_id
                ) THEN UPDATE SET
                    advertiser_name = source.advertiser_name,
                    amount_usd = source.amount_usd,
                    currency_code = source.currency_code,
                    source_row_hash = source.source_row_hash,
                    run_id = source.run_id,
                    source_modified_utc = source.source_modified_utc

                WHEN NOT MATCHED THEN INSERT (
                    advertiser_id,
                    week_start_date,
                    advertiser_name,
                    amount_usd,
                    currency_code,
                    source_row_hash,
                    run_id,
                    source_modified_utc
                ) VALUES (
                    source.advertiser_id,
                    source.week_start_date,
                    source.advertiser_name,
                    source.amount_usd,
                    source.currency_code,
                    source.source_row_hash,
                    source.run_id,
                    source.source_modified_utc
                )

                -- Snapshot tuần là đầy đủ: nếu bản mới không còn key cũ,
                -- xóa key đó khỏi current, nhưng history vẫn giữ quan sát cũ
                WHEN NOT MATCHED BY SOURCE
                     AND target.week_start_date = ? THEN DELETE
            """,
                [run_id, week_start],
            )

            con.execute("COMMIT")
            print(f"PROMOTED week={week_start} run={run_id} rows={valid}")

        except Exception:
            con.execute("ROLLBACK")
            raise

    total = con.execute("SELECT count(*) FROM silver.weekly_current").fetchone()[0]
    print(f"SILVER_CURRENT_TOTAL={total}")
    return total


def main() -> None:
    con = duckdb.connect(str(DB))
    promote(con)
    con.close()


if __name__ == "__main__":
    main()
