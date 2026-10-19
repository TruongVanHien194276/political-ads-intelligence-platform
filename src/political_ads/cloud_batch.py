"""Cloud-only entry point; the local raw and Airflow CLIs remain unchanged."""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from google.cloud import bigquery

from political_ads.cloud_publish import MAX_PARQUET_BYTES, publish_from_gcs
from political_ads.config import load_settings
from political_ads.raw_lake import extract_week, verify_local

RUN_ID_PATTERN = re.compile(r"[0-9a-f]{16}\Z")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Ngày phải có dạng YYYY-MM-DD") from exc


def latest_completed_week(today: date) -> date:
    """The current Sunday starts an unfinished week, even on Sunday itself."""
    this_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    return this_sunday - timedelta(days=7)


def resolve_week(
    cli_value: date | None,
    env_value: str | None,
    *,
    today: date | None = None,
) -> date:
    current_day = today or datetime.now(timezone.utc).date()
    week = cli_value or (date.fromisoformat(env_value) if env_value else None)
    week = week or latest_completed_week(current_day)

    if week.weekday() != 6:
        raise ValueError("week_start phải là Chủ nhật")
    if week > latest_completed_week(current_day):
        raise ValueError("Không chạy tuần chưa kết thúc")
    return week


def snapshot_prefix(week: date, run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id phải gồm 16 ký tự hex")
    return (
        "source=google_political_ads/table=advertiser_weekly_spend/"
        f"week_start={week.isoformat()}/run_id={run_id}"
    )


def extract_to_gcs(
    bq_client: bigquery.Client,
    *,
    week: date,
    max_bytes: int,
    execute: bool,
    bucket_name: str | None,
    storage_client=None,
) -> dict:
    if execute and not bucket_name:
        raise ValueError("RAW_BUCKET là bắt buộc khi dùng --execute")

    with tempfile.TemporaryDirectory(prefix="political-ads-cloud-") as temp:
        raw_root = Path(temp)
        _, planned_dir, plan = extract_week(
            bq_client,
            week,
            root=raw_root,
            max_bytes=max_bytes,
            execute=False,
        )
        run_id = plan["run_id"]
        prefix = snapshot_prefix(week, run_id)

        if not execute:
            return {
                "status": "DRY_RUN",
                "week_start": week.isoformat(),
                "run_id": run_id,
                "estimated_bytes": plan["estimated_bytes"],
                "max_bytes": max_bytes,
                "planned_gcs_prefix": f"gs://{bucket_name}/{prefix}"
                if bucket_name
                else None,
            }

        if storage_client is None:
            # Storage is an optional dependency for local-only installations.
            from google.cloud import storage

            storage_client = storage.Client(project=bq_client.project)

        bucket = storage_client.bucket(bucket_name)
        data_blob = bucket.blob(f"{prefix}/data.parquet")
        manifest_blob = bucket.blob(f"{prefix}/manifest.json")

        if manifest_blob.exists():
            if not data_blob.exists():
                raise RuntimeError("Có manifest GCS nhưng thiếu Parquet")
            remote_manifest = json.loads(manifest_blob.download_as_text())
            if remote_manifest.get("parquet_bytes", 0) > MAX_PARQUET_BYTES:
                raise RuntimeError("Parquet GCS vượt giới hạn 128 MiB")
            data_blob.reload()
            if data_blob.size != remote_manifest.get("parquet_bytes"):
                raise RuntimeError("Parquet GCS size không khớp manifest")
            planned_dir.mkdir(parents=True)
            manifest_blob.download_to_filename(str(planned_dir / "manifest.json"))
            data_blob.download_to_filename(str(planned_dir / "data.parquet"))
            manifest = verify_local(planned_dir, run_id)
            if (
                manifest != remote_manifest
                or manifest.get("run_id") != run_id
                or manifest.get("week_start") != week.isoformat()
            ):
                raise RuntimeError("Manifest GCS không khớp snapshot dự kiến")
            return {
                "status": "SKIPPED",
                "week_start": week.isoformat(),
                "run_id": run_id,
                "gcs_manifest": f"gs://{bucket_name}/{prefix}/manifest.json",
                "row_count": manifest["row_count"],
            }

        if data_blob.exists():
            raise RuntimeError(
                "Có Parquet GCS nhưng thiếu manifest; cần xử lý thủ công"
            )

        status, run_dir, _ = extract_week(
            bq_client,
            week,
            root=raw_root,
            max_bytes=max_bytes,
            execute=True,
        )
        if status != "CREATED" or run_dir != planned_dir:
            raise RuntimeError("Nguồn thay đổi sau dry-run; hãy chạy lại")

        manifest = verify_local(run_dir, run_id)
        if manifest["parquet_bytes"] > MAX_PARQUET_BYTES:
            raise RuntimeError("Parquet vượt giới hạn 128 MiB của cloud job")
        # Publish marker last. Neither object may silently overwrite an old one.
        data_blob.upload_from_filename(
            str(run_dir / "data.parquet"), if_generation_match=0
        )
        manifest_blob.upload_from_filename(
            str(run_dir / "manifest.json"),
            content_type="application/json",
            if_generation_match=0,
        )
        return {
            "status": "UPLOADED",
            "week_start": week.isoformat(),
            "run_id": run_id,
            "row_count": manifest["row_count"],
            "parquet_sha256": manifest["parquet_sha256"],
            "bigquery_job_id": manifest["bigquery_job_id"],
            "gcs_manifest": f"gs://{bucket_name}/{prefix}/manifest.json",
        }


def main(
    argv: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Political ads cloud batch CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("extract", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--week-start", type=parse_date)
        command.add_argument("--execute", action="store_true")

    args = parser.parse_args(argv)
    values = os.environ if environ is None else environ

    try:
        week = resolve_week(args.week_start, values.get("WEEK_START"))
        settings = load_settings(values)
        if not settings.project_id:
            raise ValueError("Thiếu GCP_PROJECT_ID")

        client = bigquery.Client(project=settings.project_id)
        if args.command == "extract":
            result = extract_to_gcs(
                client,
                week=week,
                max_bytes=settings.max_bytes_billed,
                execute=args.execute,
                bucket_name=values.get("RAW_BUCKET"),
            )
        else:
            from google.cloud import storage

            result = publish_from_gcs(
                client,
                storage_client=storage.Client(project=settings.project_id),
                project_id=settings.project_id,
                bucket_name=values.get("RAW_BUCKET", ""),
                week=week,
                max_bytes=settings.max_bytes_billed,
                execute=args.execute,
            )
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
