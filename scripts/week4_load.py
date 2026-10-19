import hashlib
import json
import os
from pathlib import Path

import duckdb

ROOT = Path("data/raw")
DB = Path(
    os.getenv(
        "WAREHOUSE_DB_PATH",
        "data/tmp/week4/warehouse.duckdb",
    )
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    # Đọc từng khối để không kéo cả file vào RAM
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    week_filter = os.getenv("RAW_WEEK_START")

    if week_filter:
        # Airflow xử lý đúng partition ứng với logical date
        runs = sorted(
            ROOT.glob(
                "source=google_political_ads/"
                "table=advertiser_weekly_spend/"
                f"week_start={week_filter}/run_id=*"
            )
        )
    else:
        runs = sorted(ROOT.glob("source=*/table=*/week_start=*/run_id=*"))

    if not runs:
        raise RuntimeError("Không thấy snapshot Tuần 3 trong data/raw")

    con = duckdb.connect(str(DB))
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 2")
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")

    con.execute("""
        CREATE TABLE IF NOT EXISTS bronze.loaded_runs (
            run_id VARCHAR PRIMARY KEY,
            snapshot_week_start DATE NOT NULL,
            source_modified_utc TIMESTAMPTZ NOT NULL,
            row_count BIGINT NOT NULL,
            parquet_sha256 VARCHAR NOT NULL
        )
    """)

    for run_dir in runs:
        parquet = run_dir / "data.parquet"
        manifest_path = run_dir / "manifest.json"

        if not parquet.is_file() or not manifest_path.is_file():
            raise RuntimeError(f"Snapshot thiếu file: {run_dir}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_id = manifest["run_id"]

        if run_dir.name != f"run_id={run_id}":
            raise RuntimeError(f"run_id không khớp tên thư mục: {run_dir}")

        actual_sha = sha256_file(parquet)
        if actual_sha != manifest["parquet_sha256"]:
            raise RuntimeError(f"Checksum không khớp: {parquet}")

        actual_rows = con.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
            [str(parquet)],
        ).fetchone()[0]

        if actual_rows != manifest["row_count"]:
            raise RuntimeError(f"Row count không khớp: {parquet}")

        previous = con.execute(
            """
            SELECT row_count, parquet_sha256
            FROM bronze.loaded_runs
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()

        if previous:
            if previous != (actual_rows, actual_sha):
                raise RuntimeError(f"run_id cũ nhưng nội dung đổi: {run_id}")
            print(f"SKIPPED {run_id}: {actual_rows} rows")
            continue

        try:
            con.execute("BEGIN TRANSACTION")

            # Tạo schema Bronze từ Parquet thật; WHERE FALSE không nạp dữ liệu
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS bronze.weekly_raw AS
                SELECT
                    p.*,
                    NULL::VARCHAR AS run_id,
                    NULL::DATE AS snapshot_week_start,
                    NULL::TIMESTAMPTZ AS source_modified_utc,
                    NULL::VARCHAR AS source_row_hash,
                    NULL::BIGINT AS row_in_run
                FROM read_parquet(?, hive_partitioning=false) AS p
                WHERE FALSE
            """,
                [str(parquet)],
            )

            # Hash chỉ tính các cột nguồn p.*, không tính run_id
            # Nhờ vậy có thể nhận biết nội dung thực sự được sửa giữa hai run
            con.execute(
                """
                INSERT INTO bronze.weekly_raw
                SELECT
                    p.*,
                    ?::VARCHAR AS run_id,
                    ?::DATE AS snapshot_week_start,
                    ?::TIMESTAMPTZ AS source_modified_utc,
                    sha256(to_json(p)) AS source_row_hash,
                    row_number() OVER () AS row_in_run
                FROM read_parquet(?, hive_partitioning=false) AS p
            """,
                [
                    run_id,
                    manifest["week_start"],
                    manifest["source_modified_utc"],
                    str(parquet),
                ],
            )

            con.execute(
                """
                INSERT INTO bronze.loaded_runs
                VALUES (?, ?::DATE, ?::TIMESTAMPTZ, ?, ?)
            """,
                [
                    run_id,
                    manifest["week_start"],
                    manifest["source_modified_utc"],
                    actual_rows,
                    actual_sha,
                ],
            )

            con.execute("COMMIT")
            print(f"CREATED {run_id}: {actual_rows} rows")

        except Exception:
            con.execute("ROLLBACK")
            raise

    total = con.execute("SELECT count(*) FROM bronze.weekly_raw").fetchone()[0]
    print(f"BRONZE_TOTAL={total}")
    con.close()


if __name__ == "__main__":
    main()
