import json
import os
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

SOURCE_DATASET = "bigquery-public-data.google_political_ads"
OUTPUT_PATH = Path("reports/week1/source_inventory.json")


def main() -> None:
    # Ưu tiên biến môi trường; nếu chưa export thì BigQuery lấy project từ ADC
    client = bigquery.Client(project=os.getenv("GCP_PROJECT_ID") or None)

    dataset = client.get_dataset(SOURCE_DATASET)

    inventory = {
        "source_dataset": SOURCE_DATASET,
        "location": dataset.location,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "tables": [],
    }

    for item in sorted(client.list_tables(dataset), key=lambda x: x.table_id):
        table = client.get_table(item.reference)

        inventory["tables"].append(
            {
                "name": table.table_id,
                "table_type": table.table_type,
                "estimated_rows": table.num_rows,
                "size_bytes": table.num_bytes,
                "last_modified_utc": (
                    table.modified.isoformat() if table.modified else None
                ),
                "columns": [
                    {
                        "name": field.name,
                        "type": field.field_type,
                        "mode": field.mode,
                        "description": field.description,
                    }
                    for field in table.schema
                ],
            }
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Query project: {client.project}")
    print(f"Dataset location: {dataset.location}")
    print(f"Found {len(inventory['tables'])} tables")

    for table in inventory["tables"]:
        print(
            f"- {table['name']}: "
            f"{table['estimated_rows']} rows, "
            f"{len(table['columns'])} columns, "
            f"modified={table['last_modified_utc']}"
        )

    print(f"Saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
