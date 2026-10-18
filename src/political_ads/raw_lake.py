import hashlib
import json
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery

SOURCE_TABLE = "bigquery-public-data.google_political_ads.advertiser_weekly_spend"
EXTRACT_VERSION = "weekly-v1"
BATCH_ROWS = 1_000

SQL = f"""
SELECT *
FROM `{SOURCE_TABLE}`
WHERE week_start_date >= @week_start
  AND week_start_date < @week_end
ORDER BY advertiser_id, week_start_date
""".strip()

ARROW_TYPES = {
    "STRING": pa.string(),
    "INTEGER": pa.int64(),
    "DATE": pa.date32(),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    # Không đọc cả file Parquet vào RAM
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def arrow_schema(bq_fields) -> pa.Schema:
    fields = []

    for field in bq_fields:
        if field.field_type not in ARROW_TYPES:
            raise ValueError(
                f"Schema mới chưa được xử lý: {field.name}={field.field_type}"
            )

        fields.append(
            pa.field(field.name, ARROW_TYPES[field.field_type], nullable=True)
        )

    return pa.schema(fields)


def query_config(
    week_start: date,
    week_end: date,
    *,
    dry_run: bool,
    max_bytes: int | None = None,
) -> bigquery.QueryJobConfig:
    config = bigquery.QueryJobConfig(
        dry_run=dry_run,
        use_query_cache=False,
        query_parameters=[
            bigquery.ScalarQueryParameter("week_start", "DATE", week_start),
            bigquery.ScalarQueryParameter("week_end", "DATE", week_end),
        ],
    )
    # BigQuery rejects a serialized "None"; omit the limit for the estimate job.
    if max_bytes is not None:
        config.maximum_bytes_billed = max_bytes
    return config


def verify_local(run_dir: Path, expected_run_id: str) -> dict:
    manifest_path = run_dir / "manifest.json"
    parquet_path = run_dir / "data.parquet"

    if not manifest_path.is_file() or not parquet_path.is_file():
        raise RuntimeError(f"Snapshot chưa hoàn chỉnh, không ghi đè: {run_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest["run_id"] != expected_run_id:
        raise RuntimeError("run_id trong manifest không khớp thư mục")

    if sha256_file(parquet_path) != manifest["parquet_sha256"]:
        raise RuntimeError(f"Checksum Parquet sai: {parquet_path}")

    if pq.read_metadata(parquet_path).num_rows != manifest["row_count"]:
        raise RuntimeError(f"Row count Parquet sai: {parquet_path}")

    return manifest


def extract_week(
    client: bigquery.Client,
    week_start: date,
    *,
    root: Path,
    max_bytes: int,
    execute: bool,
) -> tuple[str, Path, dict]:
    if week_start.weekday() != 6:
        raise ValueError("week_start phải là Chủ nhật")

    week_end = week_start + timedelta(days=7)
    source_before = client.get_table(SOURCE_TABLE)
    source_modified = source_before.modified.isoformat()

    query_sha256 = hashlib.sha256(
        f"{EXTRACT_VERSION}\n{SQL}".encode("utf-8")
    ).hexdigest()

    run_id = hashlib.sha256(
        f"{week_start.isoformat()}|{source_modified}|{query_sha256}".encode()
    ).hexdigest()[:16]

    parent = (
        root
        / "source=google_political_ads"
        / "table=advertiser_weekly_spend"
        / f"week_start={week_start.isoformat()}"
    )
    run_dir = parent / f"run_id={run_id}"

    # Cùng tuần + cùng phiên bản nguồn: không chạy query lần nữa
    if run_dir.exists():
        return "SKIPPED", run_dir, verify_local(run_dir, run_id)

    dry_job = client.query(
        SQL,
        location="US",
        job_config=query_config(week_start, week_end, dry_run=True),
    )
    estimated_bytes = dry_job.total_bytes_processed or 0

    if estimated_bytes > max_bytes:
        raise RuntimeError(
            f"Dry-run cần {estimated_bytes:,} bytes, vượt giới hạn {max_bytes:,} bytes"
        )

    if not execute:
        return (
            "DRY_RUN",
            run_dir,
            {
                "week_start": week_start.isoformat(),
                "run_id": run_id,
                "estimated_bytes": estimated_bytes,
                "max_bytes": max_bytes,
            },
        )

    parent.mkdir(parents=True, exist_ok=True)

    # Ghi vào thư mục tạm cùng filesystem, chỉ publish khi đã có đủ 2 file
    stage_dir = Path(tempfile.mkdtemp(prefix=".stage-", dir=parent))

    try:
        job = client.query(
            SQL,
            location="US",
            job_config=query_config(
                week_start,
                week_end,
                dry_run=False,
                max_bytes=max_bytes,
            ),
        )
        rows = job.result(page_size=BATCH_ROWS)
        # QueryJob.schema may remain None; RowIterator carries the result schema.
        if not rows.schema:
            raise RuntimeError(f"BigQuery không trả schema cho job {job.job_id}")
        schema = arrow_schema(rows.schema)

        parquet_path = stage_dir / "data.parquet"
        writer = pq.ParquetWriter(parquet_path, schema, compression="snappy")
        batch = []
        row_count = 0

        try:
            for row in rows:
                batch.append(dict(row.items()))

                if len(batch) == BATCH_ROWS:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    row_count += len(batch)
                    batch.clear()

            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                row_count += len(batch)

            if row_count == 0:
                writer.write_table(pa.Table.from_pylist([], schema=schema))
        finally:
            writer.close()

        # Nguồn đổi trong lúc extract thì không publish snapshot mơ hồ
        source_after = client.get_table(SOURCE_TABLE)
        if source_after.modified != source_before.modified:
            raise RuntimeError("Nguồn đổi trong lúc extract, hãy chạy lại")

        manifest = {
            "run_id": run_id,
            "extract_version": EXTRACT_VERSION,
            "source_table": SOURCE_TABLE,
            "source_modified_utc": source_modified,
            "week_start": week_start.isoformat(),
            "week_end_exclusive": week_end.isoformat(),
            "query_sha256": query_sha256,
            "bigquery_job_id": job.job_id,
            "estimated_bytes": estimated_bytes,
            "processed_bytes": job.total_bytes_processed or 0,
            "billed_bytes": job.total_bytes_billed or 0,
            "row_count": row_count,
            "parquet_bytes": parquet_path.stat().st_size,
            "parquet_sha256": sha256_file(parquet_path),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        (stage_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # Không ghi đè run_dir đã có
        if run_dir.exists():
            return "SKIPPED", run_dir, verify_local(run_dir, run_id)

        stage_dir.rename(run_dir)
        return "CREATED", run_dir, manifest
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
