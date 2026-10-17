import argparse
import json
import os
from itertools import islice
from pathlib import Path

from google.cloud import bigquery


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sql", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-mib", type=int, default=512)
    parser.add_argument("--max-rows", type=int, default=500)
    args = parser.parse_args()

    if args.max_mib <= 0 or not 1 <= args.max_rows <= 1000:
        parser.error("--max-mib phải > 0 và --max-rows phải từ 1 đến 1000")

    sql = args.sql.read_text(encoding="utf-8")

    # File Tuần 1 chỉ có một câu SELECT/WITH, tránh chạy nhiều statement
    if ";" in sql or not sql.lstrip().upper().startswith(("SELECT", "WITH")):
        raise SystemExit("Chỉ chấp nhận một query SELECT hoặc WITH, không có dấu ;")

    # Khi không export GCP_PROJECT_ID, BigQuery dùng project mặc định từ ADC
    client = bigquery.Client(project=os.getenv("GCP_PROJECT_ID") or None)
    dataset = client.get_dataset(
        "bigquery-public-data.google_political_ads"
    )
    location = dataset.location
    max_bytes = args.max_mib * 1024 * 1024

    dry_job = client.query(
        sql,
        location=location,
        job_config=bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
        ),
    )

    estimated = dry_job.total_bytes_processed or 0

    print(f"Query: {args.sql}")
    print(f"Query project: {client.project}")
    print(f"Location: {location}")
    print(f"Estimated bytes: {estimated:,}")
    print(f"Limit bytes: {max_bytes:,}")

    if not args.execute:
        print("Dry-run only, chưa đọc dữ liệu")
        return

    if estimated > max_bytes:
        raise SystemExit(
            "Query vượt giới hạn, xem lại cột/filter hoặc tăng limit "
            "sau khi đã hiểu chi phí"
        )

    job = client.query(
        sql,
        location=location,
        job_config=bigquery.QueryJobConfig(
            maximum_bytes_billed=max_bytes,
            use_query_cache=False,
        ),
    )

    # Chỉ lấy một số dòng giới hạn về Mac; schema hiện có hơn 100 cột
    rows = [dict(row.items()) for row in islice(job.result(), args.max_rows)]

    print(f"Job ID: {job.job_id}")
    print(f"Processed bytes: {job.total_bytes_processed or 0:,}")
    print(f"Billed bytes: {job.total_bytes_billed or 0:,}")
    print(json.dumps(rows[:10], ensure_ascii=False, indent=2, default=str))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
