import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from political_ads.local_publish import publish_local
from political_ads.raw_lake import sha256_file, verify_local


def test_publish_is_verified_and_idempotent(tmp_path) -> None:
    source_root = tmp_path / "raw"
    source = source_root / "week_start=2026-10-04" / "run_id=abc123"
    source.mkdir(parents=True)
    pq.write_table(pa.table({"advertiser_id": ["adv-1"]}), source / "data.parquet")
    manifest = {
        "run_id": "abc123",
        "row_count": 1,
        "parquet_sha256": sha256_file(source / "data.parquet"),
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    destination_root = tmp_path / "lake"
    first_status, first_path, _ = publish_local(
        source, source_root=source_root, destination_root=destination_root
    )
    second_status, second_path, _ = publish_local(
        source, source_root=source_root, destination_root=destination_root
    )

    assert (first_status, second_status) == ("CREATED", "SKIPPED")
    assert first_path == second_path
    assert verify_local(first_path, "abc123")["row_count"] == 1

    (first_path / "data.parquet").write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="Checksum"):
        publish_local(
            source, source_root=source_root, destination_root=destination_root
        )
