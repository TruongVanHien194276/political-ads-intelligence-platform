"""Publish one immutable GCS snapshot into BigQuery Silver and Gold."""

import hashlib
import json
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Template

import pyarrow as pa
import pyarrow.parquet as pq
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from political_ads.raw_lake import EXTRACT_VERSION, SOURCE_TABLE, SQL, verify_local

PROJECT_PATTERN = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{16}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SQL_DIR = Path(__file__).resolve().parent / "sql/cloud"

SOURCE_FIELDS = (
    "advertiser_id",
    "advertiser_name",
    "election_cycle",
    "week_start_date",
    "spend_usd",
    "spend_eur",
    "spend_inr",
    "spend_bgn",
    "spend_czk",
    "spend_dkk",
    "spend_huf",
    "spend_pln",
    "spend_ron",
    "spend_sek",
    "spend_gbp",
    "spend_nzd",
    "spend_ils",
    "spend_aud",
    "spend_twd",
    "spend_brl",
    "spend_ars",
    "spend_zar",
    "spend_clp",
    "spend_mxn",
)
SPEND_FIELDS = SOURCE_FIELDS[4:]
EXPECTED_QUERY_SHA256 = hashlib.sha256(
    f"{EXTRACT_VERSION}\n{SQL}".encode("utf-8")
).hexdigest()
MAX_PARQUET_BYTES = 128 * 1024 * 1024


def checked_project(project_id: str) -> str:
    if not PROJECT_PATTERN.fullmatch(project_id):
        raise ValueError("GCP_PROJECT_ID không hợp lệ")
    return project_id


def checked_manifest(manifest: dict, week: date, path: str) -> dict:
    run_id = manifest.get("run_id", "")
    expected_suffix = f"/run_id={run_id}/manifest.json"
    if not RUN_ID_PATTERN.fullmatch(run_id) or not path.endswith(expected_suffix):
        raise ValueError(f"GCS manifest có run_id/path sai: {path}")
    if manifest.get("week_start") != week.isoformat():
        raise ValueError(f"GCS manifest sai tuần: {path}")
    if manifest.get("week_end_exclusive") != (week + timedelta(days=7)).isoformat():
        raise ValueError(f"GCS manifest sai week_end: {path}")
    if manifest.get("source_table") != SOURCE_TABLE:
        raise ValueError(f"GCS manifest sai nguồn: {path}")
    if not SHA256_PATTERN.fullmatch(manifest.get("parquet_sha256", "")):
        raise ValueError(f"GCS manifest thiếu checksum: {path}")
    if manifest.get("query_sha256") != EXPECTED_QUERY_SHA256:
        raise ValueError(f"GCS manifest dùng query khác phiên bản hiện tại: {path}")
    if type(manifest.get("row_count")) is not int or manifest["row_count"] <= 0:
        raise ValueError(f"GCS manifest có row_count không hợp lệ: {path}")
    size = manifest.get("parquet_bytes")
    if type(size) is not int or not 0 < size <= MAX_PARQUET_BYTES:
        raise ValueError(f"GCS Parquet vượt giới hạn 128 MiB: {path}")
    try:
        modified = datetime.fromisoformat(manifest["source_modified_utc"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"GCS manifest có source_modified_utc sai: {path}") from exc
    if modified.utcoffset() is None:
        raise ValueError(f"GCS manifest thiếu timezone: {path}")
    return manifest


def select_snapshot(bucket, week: date) -> tuple[dict, str]:
    prefix = (
        "source=google_political_ads/table=advertiser_weekly_spend/"
        f"week_start={week.isoformat()}/"
    )
    candidates = []
    for blob in bucket.list_blobs(prefix=prefix):
        if not blob.name.endswith("/manifest.json"):
            continue
        manifest = checked_manifest(
            json.loads(blob.download_as_text()), week, blob.name
        )
        modified = datetime.fromisoformat(manifest["source_modified_utc"])
        candidates.append((modified, manifest["run_id"], manifest, blob.name))

    if not candidates:
        raise RuntimeError(f"Không có manifest GCS cho tuần {week}")

    # Equal source version with different content is ambiguous, not a tie-break.
    by_modified: dict[datetime, str] = {}
    for modified, _, manifest, _ in candidates:
        sha = manifest["parquet_sha256"]
        if modified in by_modified and by_modified[modified] != sha:
            raise RuntimeError("Hai snapshot cùng source_modified nhưng khác nội dung")
        by_modified[modified] = sha

    _, _, manifest, manifest_name = max(candidates)
    return manifest, manifest_name.removesuffix("manifest.json")


def verify_gcs_snapshot(bucket, week: date, manifest: dict, prefix: str) -> None:
    data_blob = bucket.blob(f"{prefix}data.parquet")
    data_blob.reload()
    if data_blob.size != manifest["parquet_bytes"]:
        raise RuntimeError("GCS Parquet size không khớp manifest")
    with tempfile.TemporaryDirectory(prefix="political-ads-publish-") as temp:
        run_dir = Path(temp) / f"run_id={manifest['run_id']}"
        run_dir.mkdir()
        bucket.blob(f"{prefix}manifest.json").download_to_filename(
            str(run_dir / "manifest.json")
        )
        data_blob.download_to_filename(str(run_dir / "data.parquet"))
        actual = verify_local(run_dir, manifest["run_id"])
        if actual != manifest or actual["week_start"] != week.isoformat():
            raise RuntimeError("Manifest GCS đổi trong lúc tải")
        schema = pq.read_schema(run_dir / "data.parquet")
        expected_types = (pa.string(), pa.string(), pa.string(), pa.date32())
        if tuple(schema.names) != SOURCE_FIELDS:
            raise RuntimeError("Schema drift: tên hoặc thứ tự cột Parquet đã đổi")
        if tuple(field.type for field in list(schema)[:4]) != expected_types:
            raise RuntimeError("Schema drift: kiểu cột định danh/ngày đã đổi")
        if any(field.type != pa.int64() for field in list(schema)[4:]):
            raise RuntimeError("Schema drift: kiểu cột spend đã đổi")


def render_sql(name: str, *, project_id: str, run_id: str = "") -> str:
    project = checked_project(project_id)
    if run_id and not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id không hợp lệ")
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    return Template(text).substitute(
        PROJECT=project,
        SNAPSHOT=f"snapshot_{run_id}",
        NEGATIVE_ANY=" OR ".join(f"t.{column} < 0" for column in SPEND_FIELDS),
    )


def parameters(manifest: dict, week: date) -> list[bigquery.ScalarQueryParameter]:
    return [
        bigquery.ScalarQueryParameter("run_id", "STRING", manifest["run_id"]),
        bigquery.ScalarQueryParameter("week_start", "DATE", week),
        bigquery.ScalarQueryParameter(
            "source_modified",
            "TIMESTAMP",
            datetime.fromisoformat(manifest["source_modified_utc"]),
        ),
        bigquery.ScalarQueryParameter("expected_rows", "INT64", manifest["row_count"]),
        bigquery.ScalarQueryParameter(
            "parquet_sha256", "STRING", manifest["parquet_sha256"]
        ),
    ]


def query_job(client, sql: str, *, max_bytes: int, params=()):
    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=max_bytes,
        query_parameters=list(params),
        use_query_cache=False,
    )
    return client.query(sql, location="US", job_config=config)


def check_previous_runs(client, project_id: str, manifest: dict, week: date) -> bool:
    sql = f"""
    SELECT run_id, status, parquet_sha256, source_modified_utc
    FROM `{project_id}.political_ads_ops.pipeline_runs`
    WHERE week_start_date = @week_start
    """
    config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("week_start", "DATE", week)],
        maximum_bytes_billed=10 * 1024 * 1024,
    )
    rows = list(client.query(sql, location="US", job_config=config).result())
    selected_modified = datetime.fromisoformat(manifest["source_modified_utc"])
    already_published = False
    for row in rows:
        if row["status"] != "PASS":
            continue
        old_modified = row["source_modified_utc"]
        if old_modified > selected_modified:
            raise RuntimeError("Snapshot cũ hơn bản đã publish")
        if (
            old_modified == selected_modified
            and row["parquet_sha256"] != manifest["parquet_sha256"]
        ):
            raise RuntimeError("Cùng source_modified nhưng checksum khác")
        if row["run_id"] == manifest["run_id"]:
            if row["parquet_sha256"] != manifest["parquet_sha256"]:
                raise RuntimeError("run_id đã publish nhưng checksum đổi")
            already_published = True
    return already_published


def load_bronze(client, project_id: str, bucket_name: str, prefix: str, manifest):
    table_id = f"{project_id}.political_ads_bronze.snapshot_{manifest['run_id']}"
    description = f"parquet_sha256={manifest['parquet_sha256']}"
    try:
        table = client.get_table(table_id)
    except NotFound:
        table = None

    if table is not None:
        if table.num_rows != manifest["row_count"] or table.description != description:
            raise RuntimeError("Bronze snapshot cũ không khớp manifest")
        return table_id, None

    config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_EMPTY,
        destination_table_description=description,
    )
    job = client.load_table_from_uri(
        f"gs://{bucket_name}/{prefix}data.parquet",
        table_id,
        location="US",
        job_config=config,
    )
    job.result()
    table = client.get_table(table_id)
    if table.num_rows != manifest["row_count"] or table.description != description:
        raise RuntimeError("Bronze load xong nhưng row count/checksum không khớp")
    return table_id, job.job_id


def write_failure(
    client, project_id: str, manifest: dict, week: date, error: Exception
):
    sql = f"""
    MERGE `{project_id}.political_ads_ops.pipeline_runs` AS target
    USING (SELECT @run_id AS run_id) AS source
    ON target.run_id = source.run_id
    WHEN MATCHED AND target.status != 'PASS' THEN UPDATE SET
      status = 'FAIL', error_message = @error_message,
      updated_at = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT (
      run_id, week_start_date, source_modified_utc, parquet_sha256,
      row_count, status, error_message, updated_at
    ) VALUES (
      @run_id, @week_start, @source_modified, @parquet_sha256,
      @expected_rows, 'FAIL', @error_message, CURRENT_TIMESTAMP()
    )
    """
    params = parameters(manifest, week) + [
        bigquery.ScalarQueryParameter("error_message", "STRING", str(error)[:1000])
    ]
    query_job(client, sql, max_bytes=10 * 1024 * 1024, params=params).result()


def publish_from_gcs(
    client,
    *,
    storage_client,
    project_id: str,
    bucket_name: str,
    week: date,
    max_bytes: int,
    execute: bool,
) -> dict:
    project_id = checked_project(project_id)
    if not bucket_name:
        raise ValueError("Thiếu RAW_BUCKET")
    bucket = storage_client.bucket(bucket_name)
    manifest, prefix = select_snapshot(bucket, week)
    verify_gcs_snapshot(bucket, week, manifest, prefix)
    result = {
        "week_start": week.isoformat(),
        "run_id": manifest["run_id"],
        "row_count": manifest["row_count"],
        "gcs_manifest": f"gs://{bucket_name}/{prefix}manifest.json",
    }
    if not execute:
        return {"status": "DRY_RUN", **result}

    schema_job = query_job(
        client,
        render_sql("00_schema.sql", project_id=project_id),
        max_bytes=max_bytes,
    )
    schema_job.result()
    if check_previous_runs(client, project_id, manifest, week):
        return {"status": "SKIPPED", **result}

    try:
        bronze_table, load_job_id = load_bronze(
            client, project_id, bucket_name, prefix, manifest
        )
        job = query_job(
            client,
            render_sql(
                "01_publish.sql", project_id=project_id, run_id=manifest["run_id"]
            ),
            max_bytes=max_bytes,
            params=parameters(manifest, week),
        )
        summary = dict(next(iter(job.result())).items())
    except Exception as exc:
        # Keep the original pipeline failure even if best-effort audit also fails.
        try:
            write_failure(client, project_id, manifest, week, exc)
        except Exception:
            pass
        raise

    return {
        "status": "PUBLISHED",
        **result,
        "bronze_table": bronze_table,
        "bronze_load_job_id": load_job_id,
        "publish_job_id": job.job_id,
        **summary,
    }
