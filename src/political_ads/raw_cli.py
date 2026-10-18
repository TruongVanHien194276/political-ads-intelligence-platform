import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from google.cloud import bigquery

from political_ads.config import load_settings
from political_ads.raw_lake import extract_week


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def weeks_to_run(args) -> list[date]:
    if args.week_start:
        weeks = [args.week_start]
    elif args.backfill_start:
        if args.backfill_end is None:
            raise ValueError("--backfill-start cần --backfill-end")

        if args.backfill_end < args.backfill_start:
            raise ValueError("--backfill-end phải >= --backfill-start")

        weeks = []
        current = args.backfill_start

        while current <= args.backfill_end:
            weeks.append(current)
            current += timedelta(days=7)
    else:
        run_date = args.run_date or date.today()

        # Nguồn dùng tuần bắt đầu Chủ nhật; chỉ lấy tuần đã kết thúc
        this_sunday = run_date - timedelta(days=(run_date.weekday() + 1) % 7)
        weeks = [this_sunday - timedelta(days=7)]

    if len(weeks) > 4:
        raise ValueError("Mỗi lệnh chỉ backfill tối đa 4 tuần")

    if any(week.weekday() != 6 for week in weeks):
        raise ValueError("Các ngày week_start phải là Chủ nhật")

    return weeks


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly raw Parquet extractor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-date", type=parse_date)
    group.add_argument("--week-start", type=parse_date)
    group.add_argument("--backfill-start", type=parse_date)

    parser.add_argument("--backfill-end", type=parse_date)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.backfill_end and not args.backfill_start:
        parser.error("--backfill-end cần --backfill-start")

    settings = load_settings()
    client = bigquery.Client(project=settings.project_id)

    for week in weeks_to_run(args):
        status, run_dir, info = extract_week(
            client,
            week,
            root=Path("data/raw"),
            max_bytes=settings.max_bytes_billed,
            execute=args.execute,
        )
        print(
            json.dumps(
                {
                    "status": status,
                    "run_dir": str(run_dir),
                    **info,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    main()
