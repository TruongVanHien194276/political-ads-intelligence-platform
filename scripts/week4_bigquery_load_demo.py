import argparse
import json
import os
import subprocess
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from political_ads.raw_lake import verify_local

RUN_DIR = Path(
    "data/raw/source=google_political_ads/"
    "table=advertiser_weekly_spend/"
    "week_start=2026-10-04/"
    "run_id=f6c333bcf330ef62"
)


def require_unbilled_project(project: str) -> None:
    result = subprocess.run(
        [
            "gcloud",
            "billing",
            "projects",
            "describe",
            project,
            "--format=json(billingEnabled)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if json.loads(result.stdout).get("billingEnabled") is not False:
        raise RuntimeError("Chỉ chạy demo trên project không gắn billing")


def main() -> None:
    parser = argparse.ArgumentParser(description="BigQuery Sandbox load demo")
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    args = parser.parse_args()

    run_dir = args.run_dir
    manifest = verify_local(run_dir, run_dir.name.removeprefix("run_id="))
    project = os.environ["GCP_PROJECT_ID"]
    require_unbilled_project(project)

    client = bigquery.Client(project=project)
    dataset_id = f"{project}.political_ads_bronze_demo"
    table_id = f"{dataset_id}.raw_{manifest['run_id']}"

    dataset = bigquery.Dataset(dataset_id)
    dataset.location = "US"
    client.create_dataset(dataset, exists_ok=True)

    try:
        existing = client.get_table(table_id)
        if existing.num_rows != manifest["row_count"]:
            raise RuntimeError(
                f"BigQuery có {existing.num_rows} dòng, "
                f"manifest cần {manifest['row_count']}: {table_id}"
            )
        print("SKIPPED", table_id, existing.num_rows)
        return
    except NotFound:
        pass

    config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_EMPTY,
    )

    with (run_dir / "data.parquet").open("rb") as source:
        job = client.load_table_from_file(
            source,
            table_id,
            location="US",
            job_config=config,
        )

    job.result()
    loaded = client.get_table(table_id)
    if loaded.num_rows != manifest["row_count"]:
        raise RuntimeError(f"BigQuery load thiếu dòng: {table_id}")
    print("CREATED", table_id, loaded.num_rows)


if __name__ == "__main__":
    main()
