import argparse
import json
from pathlib import Path

from political_ads.config import load_settings
from political_ads.extract import BigQueryBackend, run_extract


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run hoặc lấy sample nhỏ từ BigQuery"
    )
    parser.add_argument("--sql", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if args.execute and args.out is None:
        parser.error("--execute cần --out để biết sample sẽ được lưu ở đâu")

    settings = load_settings()
    sql = args.sql.read_text(encoding="utf-8")

    result = run_extract(
        sql,
        settings,
        BigQueryBackend(settings.project_id),
        execute=args.execute,
    )

    print(f"Estimated bytes: {result.estimated_bytes:,}")
    print(f"Limit bytes: {settings.max_bytes_billed:,}")
    print(f"Location: {settings.location}")

    if not result.executed:
        print("Dry-run only, chưa đọc dữ liệu")
        return

    assert args.out is not None
    args.out.parent.mkdir(parents=True, exist_ok=True)

    with args.out.open("w", encoding="utf-8") as output:
        for row in result.rows:
            output.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    print(f"Rows saved: {len(result.rows)}")
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
