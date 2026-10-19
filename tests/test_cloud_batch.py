import json
from datetime import date
from pathlib import Path

import pytest

from political_ads import cloud_batch


def test_latest_completed_week_does_not_select_current_week() -> None:
    assert cloud_batch.latest_completed_week(date(2026, 10, 21)) == date(2026, 10, 11)
    assert cloud_batch.latest_completed_week(date(2026, 10, 18)) == date(2026, 10, 11)


def test_resolve_week_rejects_incomplete_and_non_sunday() -> None:
    with pytest.raises(ValueError, match="chưa kết thúc"):
        cloud_batch.resolve_week(date(2026, 10, 18), None, today=date(2026, 10, 20))

    with pytest.raises(ValueError, match="Chủ nhật"):
        cloud_batch.resolve_week(date(2026, 10, 12), None, today=date(2026, 10, 20))


def test_cli_publish_needs_project_without_cloud_calls(capsys) -> None:
    code = cloud_batch.main(
        ["publish", "--week-start", "2026-10-11", "--execute"],
        environ={},
    )
    assert code == 1
    assert "Thiếu GCP_PROJECT_ID" in capsys.readouterr().err


class FakeBlob:
    def __init__(self, objects: dict, name: str) -> None:
        self.objects = objects
        self.name = name

    def exists(self) -> bool:
        return self.name in self.objects

    def upload_from_filename(self, filename: str, **kwargs) -> None:
        assert kwargs["if_generation_match"] == 0
        assert self.name not in self.objects
        self.objects[self.name] = Path(filename).read_bytes()

    def download_as_text(self) -> str:
        return self.objects[self.name].decode()

    def download_to_filename(self, filename: str) -> None:
        Path(filename).write_bytes(self.objects[self.name])

    def reload(self) -> None:
        assert self.name in self.objects

    @property
    def size(self) -> int:
        return len(self.objects[self.name])


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.objects, name)


class FakeStorage:
    def __init__(self) -> None:
        self.fake_bucket = FakeBucket()

    def bucket(self, name: str) -> FakeBucket:
        assert name == "test-raw-bucket"
        return self.fake_bucket


def test_extract_uploads_manifest_last_and_skips_retry(monkeypatch) -> None:
    week = date(2026, 10, 11)
    run_id = "a" * 16
    calls = []

    def fake_extract(client, selected_week, *, root, max_bytes, execute):
        assert selected_week == week
        assert max_bytes == 256 * 1024 * 1024
        run_dir = root / cloud_batch.snapshot_prefix(week, run_id)
        calls.append(execute)
        if not execute:
            return (
                "DRY_RUN",
                run_dir,
                {
                    "run_id": run_id,
                    "estimated_bytes": 1000,
                },
            )
        run_dir.mkdir(parents=True)
        (run_dir / "data.parquet").write_bytes(b"parquet")
        (run_dir / "manifest.json").write_text(json.dumps(manifest))
        return "CREATED", run_dir, {}

    manifest = {
        "run_id": run_id,
        "week_start": week.isoformat(),
        "row_count": 2,
        "parquet_sha256": "1" * 64,
        "parquet_bytes": 7,
        "bigquery_job_id": "test-job",
    }
    monkeypatch.setattr(cloud_batch, "extract_week", fake_extract)
    monkeypatch.setattr(cloud_batch, "verify_local", lambda path, rid: manifest)
    storage = FakeStorage()

    first = cloud_batch.extract_to_gcs(
        object(),
        week=week,
        max_bytes=256 * 1024 * 1024,
        execute=True,
        bucket_name="test-raw-bucket",
        storage_client=storage,
    )
    assert first["status"] == "UPLOADED"
    assert calls == [False, True]
    assert len(storage.fake_bucket.objects) == 2

    second = cloud_batch.extract_to_gcs(
        object(),
        week=week,
        max_bytes=256 * 1024 * 1024,
        execute=True,
        bucket_name="test-raw-bucket",
        storage_client=storage,
    )
    assert second["status"] == "SKIPPED"
    assert calls == [False, True, False]


def test_extract_rejects_partial_gcs_snapshot(monkeypatch) -> None:
    week = date(2026, 10, 11)
    run_id = "b" * 16
    storage = FakeStorage()
    prefix = cloud_batch.snapshot_prefix(week, run_id)
    storage.fake_bucket.objects[f"{prefix}/data.parquet"] = b"incomplete"
    monkeypatch.setattr(
        cloud_batch,
        "extract_week",
        lambda client, selected_week, *, root, max_bytes, execute: (
            "DRY_RUN",
            root / prefix,
            {"run_id": run_id, "estimated_bytes": 1000},
        ),
    )

    with pytest.raises(RuntimeError, match="thiếu manifest"):
        cloud_batch.extract_to_gcs(
            object(),
            week=week,
            max_bytes=256 * 1024 * 1024,
            execute=True,
            bucket_name="test-raw-bucket",
            storage_client=storage,
        )
