import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    project_id: str | None
    location: str
    max_bytes_billed: int
    max_rows: int


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    values = os.environ if environ is None else environ

    max_mib = int(values.get("MAX_QUERY_MIB", "256"))
    max_rows = int(values.get("MAX_RESULT_ROWS", "500"))

    if not 10 <= max_mib <= 512:
        raise ValueError("MAX_QUERY_MIB phải từ 10 đến 512")

    if not 1 <= max_rows <= 1000:
        raise ValueError("MAX_RESULT_ROWS phải từ 1 đến 1000")

    return Settings(
        project_id=values.get("GCP_PROJECT_ID") or None,
        location=values.get("BIGQUERY_LOCATION", "US"),
        max_bytes_billed=max_mib * 1024 * 1024,
        max_rows=max_rows,
    )
