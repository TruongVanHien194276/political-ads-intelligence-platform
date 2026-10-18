import json
from datetime import date

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts import week4_load


def test_load_replay_keeps_bronze_append_only(tmp_path, monkeypatch) -> None:
    root = tmp_path / "raw"
    run_dir = root / "source=x" / "table=y" / "week_start=2026-10-04" / "run_id=r1"
    run_dir.mkdir(parents=True)
    parquet = run_dir / "data.parquet"
    pq.write_table(
        pa.table({"advertiser_id": ["a"], "week_start_date": [date(2026, 10, 4)]}),
        parquet,
    )
    manifest = {
        "run_id": "r1",
        "week_start": "2026-10-04",
        "source_modified_utc": "2026-10-18T00:00:00+00:00",
        "row_count": 1,
        "parquet_sha256": week4_load.sha256_file(parquet),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    db_path = tmp_path / "warehouse.duckdb"
    monkeypatch.setattr(week4_load, "ROOT", root)
    monkeypatch.setattr(week4_load, "DB", db_path)

    week4_load.main()
    week4_load.main()
    with duckdb.connect(str(db_path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM bronze.weekly_raw").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM bronze.loaded_runs").fetchone()[0] == 1

    manifest["parquet_sha256"] = "bad"
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Checksum"):
        week4_load.main()
