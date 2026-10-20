from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import duckdb
import mlflow
import numpy as np
import pandas as pd
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from political_ads.naive_forecast import NaiveSpendModel  # noqa: E402

DB = ROOT / "data/tmp/week4/warehouse.duckdb"
FEATURE_SQL = ROOT / "sql/week11/features.sql"
W12_EVIDENCE = ROOT / "reports/week12/evidence.json"
MLFLOW_DB = ROOT / "data/tmp/week11/mlflow.db"
NAME = "political_ads_weekly_spend_naive"


def load_snapshot(cutoff: pd.Timestamp):
    evidence = json.loads(W12_EVIDENCE.read_text())
    if evidence["retained_model"] != "naive_last_week":
        raise RuntimeError("Model được chọn ở Tuần 12 đã đổi; dừng để kiểm tra")

    con = duckdb.connect(str(DB), read_only=True)
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 2")

    latest_audit = con.execute("""
        SELECT run_id, status
        FROM audit.pipeline_runs
        ORDER BY finished_at_utc DESC
        LIMIT 1
    """).fetchone()
    if not latest_audit or latest_audit[1] != "PASS":
        raise RuntimeError("Quality gate mới nhất chưa PASS")

    blocking_failures = con.execute(
        """
        SELECT COUNT(*)
        FROM audit.dq_scorecard
        WHERE run_id = ?
          AND severity = 'BLOCK'
          AND status = 'FAIL'
    """,
        [latest_audit[0]],
    ).fetchone()[0]
    if blocking_failures:
        raise RuntimeError("Có quality check mức BLOCK bị FAIL")

    gold_latest = pd.Timestamp(
        con.execute("""
            SELECT MAX(week_start_date)
            FROM fct_advertiser_weekly_spend
        """).fetchone()[0]
    )
    if cutoff > gold_latest:
        raise ValueError(f"Cutoff {cutoff.date()} mới hơn Gold {gold_latest.date()}")

    # SQL Tuần 11 chọn cohort bằng 8 tuần đầu và tạo lag từ quá khứ
    features = con.execute(FEATURE_SQL.read_text()).df()
    features["week_start_date"] = pd.to_datetime(features["week_start_date"])
    features = features[features["week_start_date"] <= cutoff].copy()

    cohort = features[["advertiser_id"]].drop_duplicates()
    if len(cohort) != 100:
        raise RuntimeError(f"Cohort đã đổi: {len(cohort)} advertiser")

    con.register("cohort_ids", cohort)
    history = con.execute(
        """
        SELECT
            f.advertiser_id,
            f.week_start_date,
            f.amount_usd,
            f.source_row_hash
        FROM fct_advertiser_weekly_spend AS f
        JOIN cohort_ids AS c USING (advertiser_id)
        WHERE f.week_start_date <= ?
        ORDER BY f.week_start_date, f.advertiser_id
    """,
        [cutoff.date()],
    ).df()
    con.close()

    # Fingerprint chỉ gồm các Gold rows có thể ảnh hưởng model đến cutoff
    fingerprint = hashlib.sha256(
        history.to_csv(index=False).encode("utf-8")
    ).hexdigest()

    return features, history, fingerprint, gold_latest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    cutoff = pd.Timestamp(args.cutoff)
    if cutoff.weekday() != 6:
        raise ValueError("week_start_date trong project phải là Chủ nhật")

    features, history, fingerprint, gold_latest = load_snapshot(cutoff)
    if args.promote and cutoff != gold_latest:
        raise ValueError("Chỉ được gắn alias Champion cho cutoff Gold mới nhất")

    residual = features["y"].to_numpy(float) - features["lag_1"].to_numpy(float)
    q10, q50, q90 = np.quantile(residual, [0.1, 0.5, 0.9])

    uri = f"sqlite:///{MLFLOW_DB.resolve()}"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    mlflow.set_experiment("political_ads_week13")
    client = MlflowClient()

    # Rerun cùng cutoff + cùng dữ liệu thì dùng lại version cũ
    try:
        client.get_registered_model(NAME)
        versions = client.search_model_versions(f"name = '{NAME}'")
    except MlflowException:
        versions = []

    for version in versions:
        if (
            version.tags.get("data_cutoff_week") == cutoff.date().isoformat()
            and version.tags.get("source_fingerprint") == fingerprint
        ):
            if args.promote:
                client.set_registered_model_alias(NAME, "Champion", version.version)
            print(
                json.dumps(
                    {
                        "status": "REUSED",
                        "version": version.version,
                        "run_id": version.run_id,
                        "source_fingerprint": fingerprint,
                    },
                    indent=2,
                )
            )
            return

    with mlflow.start_run(run_name=f"naive_{cutoff.date()}") as run:
        mlflow.log_params(
            {
                "model_family": "naive_last_week",
                "data_cutoff_week": cutoff.date().isoformat(),
                "source_fingerprint": fingerprint,
                "cohort_advertisers": 100,
                "calibration_rows": len(features),
            }
        )
        mlflow.log_metrics(
            {
                "residual_q10": float(q10),
                "residual_q50": float(q50),
                "residual_q90": float(q90),
            }
        )

        logged = mlflow.pyfunc.log_model(
            name="naive_forecast",
            python_model=NaiveSpendModel(float(q10), float(q50), float(q90)),
            code_paths=[str(ROOT / "src")],
            input_example=pd.DataFrame({"lag_1": [1000.0]}),
            pip_requirements=[
                "mlflow==3.17.1",
                "numpy==2.5.4",
                "pandas==3.0.6",
            ],
        )
        version = mlflow.register_model(logged.model_uri, NAME)

    client.set_model_version_tag(
        NAME, version.version, "data_cutoff_week", cutoff.date().isoformat()
    )
    client.set_model_version_tag(
        NAME, version.version, "source_fingerprint", fingerprint
    )
    client.set_model_version_tag(
        NAME, version.version, "model_family", "naive_last_week"
    )

    if args.promote:
        client.set_registered_model_alias(NAME, "Champion", version.version)

    print(
        json.dumps(
            {
                "status": "REGISTERED",
                "version": version.version,
                "run_id": run.info.run_id,
                "data_cutoff_week": cutoff.date().isoformat(),
                "source_fingerprint": fingerprint,
                "history_rows": len(history),
                "promoted": args.promote,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
