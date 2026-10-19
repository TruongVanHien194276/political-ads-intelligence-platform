import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from political_ads.cloud_publish import (
    EXPECTED_QUERY_SHA256,
    SOURCE_FIELDS,
    checked_manifest,
    load_bronze,
    publish_from_gcs,
    render_sql,
    select_snapshot,
)
from political_ads.raw_lake import SOURCE_TABLE

WEEK = date(2026, 10, 11)
PROJECT = "political-ads-intel-194276"
BUCKET = "test-raw-bucket"
RUN_ID = "a" * 16


class Blob:
    def __init__(self, objects: dict[str, bytes], name: str) -> None:
        self.objects = objects
        self.name = name

    def download_as_text(self) -> str:
        return self.objects[self.name].decode()

    def download_to_filename(self, filename: str) -> None:
        Path(filename).write_bytes(self.objects[self.name])

    def reload(self) -> None:
        if self.name not in self.objects:
            raise NotFound("not found")

    @property
    def size(self) -> int:
        return len(self.objects[self.name])


class Bucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def blob(self, name: str) -> Blob:
        return Blob(self.objects, name)

    def list_blobs(self, *, prefix: str):
        return [
            Blob(self.objects, name)
            for name in sorted(self.objects)
            if name.startswith(prefix)
        ]


class Storage:
    def __init__(self, bucket: Bucket) -> None:
        self.raw_bucket = bucket

    def bucket(self, name: str) -> Bucket:
        assert name == BUCKET
        return self.raw_bucket


def snapshot(bucket: Bucket, tmp_path: Path, *, run_id=RUN_ID, modified=None):
    modified = modified or "2026-10-18T08:00:00+00:00"
    fields = [
        pa.field("advertiser_id", pa.string()),
        pa.field("advertiser_name", pa.string()),
        pa.field("election_cycle", pa.string()),
        pa.field("week_start_date", pa.date32()),
        *[pa.field(name, pa.int64()) for name in SOURCE_FIELDS[4:]],
    ]
    row = {
        "advertiser_id": "adv-1",
        "advertiser_name": "Advertiser 1",
        "election_cycle": None,
        "week_start_date": WEEK,
        **{name: 0 for name in SOURCE_FIELDS[4:]},
    }
    row["spend_usd"] = 100
    parquet = tmp_path / f"{run_id}.parquet"
    pq.write_table(pa.Table.from_pylist([row], schema=pa.schema(fields)), parquet)
    data = parquet.read_bytes()
    manifest = {
        "run_id": run_id,
        "week_start": WEEK.isoformat(),
        "week_end_exclusive": "2026-10-18",
        "source_table": SOURCE_TABLE,
        "source_modified_utc": modified,
        "query_sha256": EXPECTED_QUERY_SHA256,
        "parquet_sha256": hashlib.sha256(data).hexdigest(),
        "parquet_bytes": len(data),
        "row_count": 1,
    }
    prefix = (
        "source=google_political_ads/table=advertiser_weekly_spend/"
        f"week_start={WEEK.isoformat()}/run_id={run_id}/"
    )
    bucket.objects[f"{prefix}data.parquet"] = data
    bucket.objects[f"{prefix}manifest.json"] = json.dumps(manifest).encode()
    return manifest, prefix


def test_select_latest_source_version_and_verify_snapshot(tmp_path) -> None:
    bucket = Bucket()
    snapshot(bucket, tmp_path, run_id="b" * 16, modified="2026-10-18T07:00:00+00:00")
    latest, prefix = snapshot(bucket, tmp_path)

    selected, selected_prefix = select_snapshot(bucket, WEEK)
    assert selected == latest
    assert selected_prefix == prefix

    result = publish_from_gcs(
        object(),
        storage_client=Storage(bucket),
        project_id=PROJECT,
        bucket_name=BUCKET,
        week=WEEK,
        max_bytes=256 * 1024 * 1024,
        execute=False,
    )
    assert result["status"] == "DRY_RUN"
    assert result["run_id"] == RUN_ID


def test_manifest_and_parquet_tampering_are_blocked(tmp_path) -> None:
    bucket = Bucket()
    manifest, prefix = snapshot(bucket, tmp_path)
    with pytest.raises(ValueError, match="run_id/path"):
        checked_manifest(manifest, WEEK, "wrong/manifest.json")

    bucket.objects[f"{prefix}data.parquet"] = b"x" * len(
        bucket.objects[f"{prefix}data.parquet"]
    )
    with pytest.raises(RuntimeError, match="Checksum"):
        publish_from_gcs(
            object(),
            storage_client=Storage(bucket),
            project_id=PROJECT,
            bucket_name=BUCKET,
            week=WEEK,
            max_bytes=256 * 1024 * 1024,
            execute=False,
        )


def test_schema_drift_is_blocked_before_bronze_load(tmp_path) -> None:
    bucket = Bucket()
    manifest, prefix = snapshot(bucket, tmp_path)
    drifted = tmp_path / "drifted.parquet"
    pq.write_table(pa.table({"advertiser_id": ["adv-1"]}), drifted)
    bucket.objects[f"{prefix}data.parquet"] = drifted.read_bytes()
    manifest["parquet_sha256"] = hashlib.sha256(
        bucket.objects[f"{prefix}data.parquet"]
    ).hexdigest()
    manifest["parquet_bytes"] = len(bucket.objects[f"{prefix}data.parquet"])
    bucket.objects[f"{prefix}manifest.json"] = json.dumps(manifest).encode()

    with pytest.raises(RuntimeError, match="Schema drift"):
        publish_from_gcs(
            object(),
            storage_client=Storage(bucket),
            project_id=PROJECT,
            bucket_name=BUCKET,
            week=WEEK,
            max_bytes=256 * 1024 * 1024,
            execute=False,
        )


def test_same_timestamp_different_content_is_ambiguous(tmp_path) -> None:
    bucket = Bucket()
    snapshot(bucket, tmp_path)
    manifest, prefix = snapshot(bucket, tmp_path, run_id="b" * 16)
    manifest["parquet_sha256"] = "0" * 64
    bucket.objects[f"{prefix}manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(RuntimeError, match="khác nội dung"):
        select_snapshot(bucket, WEEK)


def test_sql_has_quality_gate_before_transaction_and_validated_identifiers() -> None:
    sql = render_sql("01_publish.sql", project_id=PROJECT, run_id=RUN_ID)
    assert "snapshot_" + RUN_ID in sql
    assert "ASSERT" in sql
    assert sql.index("Quality gate failed") < sql.index("BEGIN TRANSACTION")
    assert "next_current" in sql
    assert "weekly_history" in sql
    assert "mart_bi_weekly" in sql
    assert "t.spend_mxn < 0" in sql

    with pytest.raises(ValueError, match="run_id"):
        render_sql("01_publish.sql", project_id=PROJECT, run_id="x; DROP TABLE")


class FakeJob:
    def __init__(self, rows=(), job_id="job-1", error=None) -> None:
        self.rows = rows
        self.job_id = job_id
        self.error = error

    def result(self):
        if self.error:
            raise self.error
        return self.rows


class FakeBQ:
    def __init__(self, manifest: dict, *, fail_publish=False, audit_rows=()) -> None:
        self.manifest = manifest
        self.fail_publish = fail_publish
        self.audit_rows = audit_rows
        self.table = None
        self.sql_calls = []
        self.load_calls = 0

    def query(self, sql, *, location, job_config):
        assert location == "US"
        assert isinstance(job_config, bigquery.QueryJobConfig)
        self.sql_calls.append(sql)
        if "SELECT run_id, status, parquet_sha256" in sql:
            return FakeJob(self.audit_rows)
        if "CREATE TEMP TABLE candidate" in sql:
            if self.fail_publish:
                return FakeJob(error=RuntimeError("quality gate failed"))
            row = SimpleNamespace(items=lambda: [("gold_fact_rows", 1)])
            return FakeJob([row], job_id="publish-job")
        return FakeJob([])

    def get_table(self, table_id):
        if self.table is None:
            raise NotFound("not found")
        return self.table

    def load_table_from_uri(self, uri, table_id, *, location, job_config):
        assert uri.startswith(f"gs://{BUCKET}/")
        assert location == "US"
        assert job_config.write_disposition == bigquery.WriteDisposition.WRITE_EMPTY
        self.load_calls += 1
        self.table = SimpleNamespace(
            num_rows=self.manifest["row_count"],
            description=job_config.destination_table_description,
        )
        return FakeJob(job_id="load-job")


def test_execute_loads_bronze_then_publishes_and_audits_failure(tmp_path) -> None:
    bucket = Bucket()
    manifest, _ = snapshot(bucket, tmp_path)
    client = FakeBQ(manifest)
    result = publish_from_gcs(
        client,
        storage_client=Storage(bucket),
        project_id=PROJECT,
        bucket_name=BUCKET,
        week=WEEK,
        max_bytes=256 * 1024 * 1024,
        execute=True,
    )
    assert result["status"] == "PUBLISHED"
    assert result["bronze_load_job_id"] == "load-job"
    assert result["publish_job_id"] == "publish-job"
    assert client.load_calls == 1

    failed = FakeBQ(manifest, fail_publish=True)
    with pytest.raises(RuntimeError, match="quality gate failed"):
        publish_from_gcs(
            failed,
            storage_client=Storage(bucket),
            project_id=PROJECT,
            bucket_name=BUCKET,
            week=WEEK,
            max_bytes=256 * 1024 * 1024,
            execute=True,
        )
    assert any("status = 'FAIL'" in sql for sql in failed.sql_calls)


def test_bronze_retry_rejects_changed_description() -> None:
    manifest = {"run_id": RUN_ID, "row_count": 1, "parquet_sha256": "a" * 64}
    client = FakeBQ(manifest)
    client.table = SimpleNamespace(num_rows=1, description="different")
    with pytest.raises(RuntimeError, match="không khớp"):
        load_bronze(client, PROJECT, BUCKET, "prefix/", manifest)


def test_replay_skips_passed_run_and_rejects_older_correction(tmp_path) -> None:
    bucket = Bucket()
    manifest, _ = snapshot(bucket, tmp_path)
    passed = {
        "run_id": RUN_ID,
        "status": "PASS",
        "parquet_sha256": manifest["parquet_sha256"],
        "source_modified_utc": datetime.fromisoformat(manifest["source_modified_utc"]),
    }
    client = FakeBQ(manifest, audit_rows=[passed])
    result = publish_from_gcs(
        client,
        storage_client=Storage(bucket),
        project_id=PROJECT,
        bucket_name=BUCKET,
        week=WEEK,
        max_bytes=256 * 1024 * 1024,
        execute=True,
    )
    assert result["status"] == "SKIPPED"
    assert client.load_calls == 0

    passed["source_modified_utc"] = datetime.fromisoformat("2026-10-19T08:00:00+00:00")
    with pytest.raises(RuntimeError, match="Snapshot cũ hơn"):
        publish_from_gcs(
            client,
            storage_client=Storage(bucket),
            project_id=PROJECT,
            bucket_name=BUCKET,
            week=WEEK,
            max_bytes=256 * 1024 * 1024,
            execute=True,
        )
