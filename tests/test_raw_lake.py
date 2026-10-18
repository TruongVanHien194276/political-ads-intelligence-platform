from datetime import date, datetime, timezone
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest
from google.cloud import bigquery

from political_ads.raw_lake import extract_week, query_config


class FakeJob:
    def __init__(self, dry_run: bool) -> None:
        self.total_bytes_processed = 1_000
        self.total_bytes_billed = 10_485_760
        self.job_id = "fake-job"
        self.result_schema = [
            bigquery.SchemaField("advertiser_id", "STRING"),
            bigquery.SchemaField("week_start_date", "DATE"),
            bigquery.SchemaField("spend_usd", "INTEGER"),
        ]
        self.schema = None  # Mirrors the actual job that exposed the Week 3 bug.
        self.dry_run = dry_run

    def result(self, page_size: int):
        assert page_size == 1_000
        rows = FakeRows(
            [
                {
                    "advertiser_id": "adv-1",
                    "week_start_date": date(2026, 10, 4),
                    "spend_usd": 100,
                }
            ]
        )
        rows.schema = self.result_schema
        return rows


class FakeRows(list):
    schema: list[bigquery.SchemaField]


class FakeClient:
    def __init__(self) -> None:
        self.modified = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.execute_count = 0

    def get_table(self, table_name: str):
        return SimpleNamespace(modified=self.modified)

    def query(self, sql: str, *, location: str, job_config):
        assert location == "US"

        if job_config.dry_run:
            return FakeJob(dry_run=True)

        self.execute_count += 1
        return FakeJob(dry_run=False)


def test_same_source_version_skips_second_extract(tmp_path) -> None:
    client = FakeClient()
    week = date(2026, 10, 4)

    first_status, first_dir, first = extract_week(
        client,
        week,
        root=tmp_path,
        max_bytes=256 * 1024 * 1024,
        execute=True,
    )
    second_status, second_dir, second = extract_week(
        client,
        week,
        root=tmp_path,
        max_bytes=256 * 1024 * 1024,
        execute=True,
    )

    assert first_status == "CREATED"
    assert second_status == "SKIPPED"
    assert first_dir == second_dir
    assert first["parquet_sha256"] == second["parquet_sha256"]
    assert client.execute_count == 1
    assert pq.read_metadata(first_dir / "data.parquet").num_rows == 1


def test_cost_limit_blocks_execution(tmp_path) -> None:
    client = FakeClient()

    with pytest.raises(RuntimeError, match="vượt giới hạn"):
        extract_week(
            client,
            date(2026, 10, 4),
            root=tmp_path,
            max_bytes=100,
            execute=True,
        )

    assert client.execute_count == 0


def test_dry_run_omits_maximum_bytes_billed() -> None:
    config = query_config(date(2026, 10, 4), date(2026, 10, 11), dry_run=True)
    assert "maximumBytesBilled" not in config.to_api_repr()["query"]
