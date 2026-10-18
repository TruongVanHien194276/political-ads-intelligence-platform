from dataclasses import dataclass
from typing import Any, Protocol

from google.cloud import bigquery

from political_ads.config import Settings


class CostLimitError(ValueError):
    """Query vượt ngân sách byte cho 1 lần chạy"""


class QueryBackend(Protocol):
    def estimate_bytes(self, sql: str, location: str) -> int: ...

    def fetch_rows(
        self,
        sql: str,
        location: str,
        max_bytes: int,
        max_rows: int,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ExtractResult:
    estimated_bytes: int
    executed: bool
    rows: list[dict[str, Any]]


class BigQueryBackend:
    def __init__(self, project_id: str | None) -> None:
        # project_id=None để Google client lấy project từ ADC
        self.client = bigquery.Client(project=project_id)

    def estimate_bytes(self, sql: str, location: str) -> int:
        job = self.client.query(
            sql,
            location=location,
            job_config=bigquery.QueryJobConfig(
                dry_run=True,
                use_query_cache=False,
            ),
        )
        return job.total_bytes_processed or 0

    def fetch_rows(
        self,
        sql: str,
        location: str,
        max_bytes: int,
        max_rows: int,
    ) -> list[dict[str, Any]]:
        job = self.client.query(
            sql,
            location=location,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=max_bytes,
                use_query_cache=False,
            ),
        )

        # Đây chỉ là sample nhỏ cho Tuần 2, chưa phải extractor đầy đủ
        return [dict(row.items()) for row in job.result(max_results=max_rows)]


def run_extract(
    sql: str,
    settings: Settings,
    backend: QueryBackend,
    *,
    execute: bool = False,
) -> ExtractResult:
    statement = sql.strip()

    # Các file SQL của project là query đơn; không nhận nhiều statement
    if ";" in statement or not statement.upper().startswith(("SELECT", "WITH")):
        raise ValueError("Chỉ nhận một query SELECT hoặc WITH, không có dấu ;")

    estimated = backend.estimate_bytes(statement, settings.location)

    if estimated > settings.max_bytes_billed:
        raise CostLimitError(
            f"Query cần {estimated:,} bytes, "
            f"vượt giới hạn {settings.max_bytes_billed:,} bytes"
        )

    if not execute:
        return ExtractResult(estimated_bytes=estimated, executed=False, rows=[])

    rows = backend.fetch_rows(
        statement,
        settings.location,
        settings.max_bytes_billed,
        settings.max_rows,
    )
    return ExtractResult(estimated_bytes=estimated, executed=True, rows=rows)
