import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from political_ads.raw_lake import verify_local  # noqa: E402
from scripts import week6_publish as week6  # noqa: E402

RUNS = ROOT / "data/tmp/week7/runs"


def week_for_logical_date(value: str) -> date:
    logical_date = date.fromisoformat(value)

    if logical_date.weekday() != 0:
        raise ValueError("logical date phải là thứ Hai UTC")

    return logical_date - timedelta(days=1)


def source_runs(week_start: date) -> list[Path]:
    return sorted(
        (ROOT / "data/raw").glob(
            "source=google_political_ads/"
            "table=advertiser_weekly_spend/"
            f"week_start={week_start.isoformat()}/run_id=*"
        )
    )


def latest_snapshot(week_start: date) -> tuple[Path, dict]:
    candidates = []

    for run_dir in source_runs(week_start):
        run_id = run_dir.name.removeprefix("run_id=")

        # verify_local kiểm tra manifest, checksum và row count
        manifest = verify_local(run_dir, run_id)
        modified = datetime.fromisoformat(manifest["source_modified_utc"])
        candidates.append((modified, run_id, run_dir, manifest))

    if not candidates:
        raise RuntimeError(f"Chưa có raw snapshot cho tuần {week_start}")

    _, _, run_dir, manifest = max(candidates)
    return run_dir, manifest


def selection(logical_date: str, run_id: str) -> dict:
    week_start = week_for_logical_date(logical_date)

    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise ValueError("source run ID phải là 16 ký tự hex")

    key = hashlib.sha256(f"{logical_date}|{run_id}".encode()).hexdigest()[:12]

    return {
        "logical_date": logical_date,
        "week_start": week_start.isoformat(),
        "source_run_id": run_id,
        "publish_key": key,
    }


def slot(info: dict) -> Path:
    return RUNS / info["logical_date"] / info["publish_key"]


def candidate_path(info: dict) -> Path:
    return slot(info) / "candidate.duckdb"


def already_published(key: str) -> bool:
    with duckdb.connect(str(week6.LIVE), read_only=True) as con:
        row = con.execute(
            """
            SELECT status
            FROM audit.pipeline_runs
            WHERE run_id = ?
            """,
            [key],
        ).fetchone()

    return row == ("PASS",)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for chunk in iter(
            lambda: source.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def run_command(command: list[str], db_path: Path, week: str) -> None:
    env = os.environ.copy()
    env["WAREHOUSE_DB_PATH"] = str(db_path)
    env["WEEK4_DB_PATH"] = str(db_path)
    env["RAW_WEEK_START"] = week

    print("RUN", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def blocking_failures(checks: list[dict]) -> list[str]:
    return [
        item["name"]
        for item in checks
        if item["severity"] == "BLOCK" and item["status"] == "FAIL"
    ]


def extract(logical_date: str) -> None:
    week_start = week_for_logical_date(logical_date)
    _, manifest = latest_snapshot(week_start)
    info = selection(logical_date, manifest["run_id"])

    # Dòng cuối stdout là JSON để Airflow đưa vào XCom
    print(json.dumps(info, sort_keys=True))


def load(info: dict) -> None:
    if already_published(info["publish_key"]):
        print("ALREADY_PUBLISHED")
        return

    db_path = candidate_path(info)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        # Candidate cũ của một lần retry được giữ nguyên
        with duckdb.connect(str(week6.LIVE)) as con:
            con.execute("CHECKPOINT")

        shutil.copy2(week6.LIVE, db_path)

    run_command(
        [str(week6.DBT_PYTHON), "scripts/week4_load.py"],
        db_path,
        info["week_start"],
    )
    print(f"LOADED candidate={db_path}")


def transform(info: dict, fail_once: bool) -> None:
    if already_published(info["publish_key"]):
        print("ALREADY_PUBLISHED")
        return

    db_path = candidate_path(info)
    if not db_path.is_file():
        raise RuntimeError("Chưa có candidate; chạy load trước")

    if fail_once:
        marker = slot(info) / "fail-once-transform.marker"

        if not marker.exists():
            marker.write_text("injected\n", encoding="utf-8")
            raise RuntimeError("Lỗi mô phỏng ở transform")

    run_command(
        [
            str(week6.DBT),
            "build",
            "--project-dir",
            "transform",
            "--profiles-dir",
            "transform",
            "--exclude",
            "path:models/gold",
        ],
        db_path,
        info["week_start"],
    )

    run_command(
        [str(week6.DBT_PYTHON), "scripts/week4_promote.py"],
        db_path,
        info["week_start"],
    )

    run_command(
        [
            str(week6.DBT),
            "build",
            "--project-dir",
            "transform",
            "--profiles-dir",
            "transform",
            "--select",
            "tag:gold",
        ],
        db_path,
        info["week_start"],
    )

    print("TRANSFORMED")


def quality(info: dict) -> None:
    if already_published(info["publish_key"]):
        print("ALREADY_PUBLISHED")
        return

    db_path = candidate_path(info)
    checks = week6.evaluate(db_path)
    failures = blocking_failures(checks)

    if failures:
        raise RuntimeError("Quality gate fail: " + ", ".join(failures))

    marker = slot(info) / "quality.json"
    marker.write_text(
        json.dumps(
            {
                "candidate_sha256": file_sha256(db_path),
                "checks": checks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"QUALITY_PASS checks={len(checks)} candidate={db_path}")


def publish(info: dict) -> None:
    key = info["publish_key"]

    # Retry sau khi replace thành công là NOOP
    if already_published(key):
        print(f"ALREADY_PUBLISHED key={key}")
        return

    db_path = candidate_path(info)
    marker = slot(info) / "quality.json"

    if not db_path.is_file() or not marker.is_file():
        raise RuntimeError("Thiếu candidate hoặc quality marker")

    saved = json.loads(marker.read_text(encoding="utf-8"))

    if file_sha256(db_path) != saved["candidate_sha256"]:
        raise RuntimeError("Candidate đổi sau quality gate")

    # Không publish snapshot cũ nếu raw mới xuất hiện giữa DAG run
    week_start = date.fromisoformat(info["week_start"])
    _, newest = latest_snapshot(week_start)

    if newest["run_id"] != info["source_run_id"]:
        raise RuntimeError("Nguồn đổi giữa DAG run; trigger lại logical date")

    # Tính lại quality ngay trước khi publish
    checks = week6.evaluate(db_path)
    failures = blocking_failures(checks)

    if failures:
        raise RuntimeError("Quality gate fail: " + ", ".join(failures))

    week6.write_audit(
        db_path,
        key,
        "PASS",
        f"Airflow logical_date={info['logical_date']}",
        checks,
    )

    week6.BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = week6.BACKUPS / f"{key}-{uuid.uuid4().hex[:8]}.duckdb"
    shutil.copy2(week6.LIVE, backup)

    # Candidate và live cùng filesystem; không có connection đang mở
    os.replace(db_path, week6.LIVE)

    print(f"PUBLISHED key={key}")
    print(f"BACKUP={backup}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "extract",
            "load",
            "transform",
            "quality",
            "publish",
        ],
    )
    parser.add_argument("--logical-date", required=True)
    parser.add_argument("--week-start")
    parser.add_argument("--source-run-id")
    parser.add_argument("--fail-once", action="store_true")
    args = parser.parse_args()

    if args.action == "extract":
        extract(args.logical_date)
        return

    if not args.week_start or not args.source_run_id:
        parser.error("Stage sau extract cần --week-start và --source-run-id")

    info = selection(args.logical_date, args.source_run_id)

    if info["week_start"] != args.week_start:
        parser.error("week_start không khớp logical date")

    try:
        if args.action == "load":
            load(info)
        elif args.action == "transform":
            transform(info, args.fail_once)
        elif args.action == "quality":
            quality(info)
        else:
            publish(info)

    except Exception as exc:
        # Audit failure dùng ID riêng; retry sau đó vẫn có thể PASS
        failure_id = f"{info['publish_key']}-fail-{uuid.uuid4().hex[:6]}"
        reason = week6.quarantine_reasons(candidate_path(info))

        week6.write_audit(
            week6.LIVE,
            failure_id,
            "FAIL",
            f"{args.action}: {exc}; quarantine={reason}",
            [],
        )
        raise


if __name__ == "__main__":
    main()
