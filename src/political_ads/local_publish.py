"""Publish an immutable raw snapshot to a second local lake directory."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from political_ads.raw_lake import verify_local


def publish_local(
    source_run: Path, *, source_root: Path, destination_root: Path
) -> tuple[str, Path, dict]:
    source_root = source_root.resolve()
    source_run = source_run.resolve()
    destination_root = destination_root.resolve()
    relative_run = source_run.relative_to(source_root)
    run_id = source_run.name.removeprefix("run_id=")
    if not source_run.name.startswith("run_id=") or not run_id:
        raise ValueError("Source phải là thư mục run_id=... trong data/raw")

    manifest = verify_local(source_run, run_id)
    destination = destination_root / relative_run

    if destination.exists():
        existing = verify_local(destination, run_id)
        if existing != manifest:
            raise RuntimeError(
                f"Snapshot đích khác manifest, không ghi đè: {destination}"
            )
        return "SKIPPED", destination, existing

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=destination.parent))
    try:
        shutil.copy2(source_run / "data.parquet", stage / "data.parquet")
        shutil.copy2(source_run / "manifest.json", stage / "manifest.json")
        copied = verify_local(stage, run_id)
        if copied["parquet_sha256"] != manifest["parquet_sha256"]:
            raise RuntimeError("Checksum bản sao không khớp nguồn")
        stage.rename(destination)
        return "CREATED", destination, copied
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish raw snapshot vào lake local")
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--destination-root", type=Path, default=Path("data/tmp/raw-lake")
    )
    args = parser.parse_args()

    status, destination, manifest = publish_local(
        args.source_run,
        source_root=args.source_root,
        destination_root=args.destination_root,
    )
    print(
        json.dumps(
            {
                "status": status,
                "destination": str(destination),
                "row_count": manifest["row_count"],
                "parquet_sha256": manifest["parquet_sha256"],
                "cloud_upload": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
