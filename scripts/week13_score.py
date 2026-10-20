from __future__ import annotations

import argparse
import hashlib
import json

import duckdb
import mlflow
import pandas as pd
from mlflow.tracking import MlflowClient
from week13_register import (
    MLFLOW_DB,
    NAME,
    ROOT,
    load_snapshot,
)

OUT = ROOT / "data/tmp/week13"
FORECAST_DB = OUT / "forecast.duckdb"
SCHEMA_SQL = ROOT / "sql/week13/01_forecast_mart.sql"


def digest(*parts: object) -> str:
    value = "|".join(map(str, parts))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--version")
    parser.add_argument("--retrospective", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    cutoff = pd.Timestamp(args.cutoff)
    features, history, fingerprint, gold_latest = load_snapshot(cutoff)

    if not args.retrospective and cutoff != gold_latest:
        raise ValueError("LIVE score phải dùng cutoff Gold mới nhất")

    uri = f"sqlite:///{MLFLOW_DB.resolve()}"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    client = MlflowClient()

    # Resolve alias đúng một lần, sau đó pin version cho toàn batch
    if args.version:
        version = client.get_model_version(NAME, args.version)
    elif args.retrospective:
        matches = [
            candidate
            for candidate in client.search_model_versions(f"name = '{NAME}'")
            if candidate.tags.get("data_cutoff_week") == cutoff.date().isoformat()
            and candidate.tags.get("source_fingerprint") == fingerprint
        ]
        if len(matches) != 1:
            raise ValueError("Cần đúng một model version cho cutoff hồi cứu")
        version = matches[0]
    else:
        version = client.get_model_version_by_alias(NAME, "Champion")

    if version.tags.get("data_cutoff_week") != cutoff.date().isoformat():
        raise ValueError("Model version không được calibrate cho cutoff này")
    if version.tags.get("source_fingerprint") != fingerprint:
        raise ValueError("Gold đã đổi sau lúc register; register version mới")
    if version.tags.get("model_family") != "naive_last_week":
        raise ValueError("Version không phải naive được Tuần 12 chọn")
    if not version.run_id:
        raise ValueError("Model version thiếu MLflow run ID")

    cohort = set(features["advertiser_id"].unique())
    current = history[pd.to_datetime(history["week_start_date"]) == cutoff].copy()

    if current["advertiser_id"].duplicated().any():
        raise ValueError("Duplicate advertiser ở cutoff")
    if len(current) < 0.70 * len(cohort):
        raise ValueError("Coverage input dưới 70% cohort")

    abstained = len(cohort) - len(current)
    target_week = cutoff + pd.Timedelta(days=7)
    mode = "RETROSPECTIVE" if args.retrospective else "LIVE"

    model = mlflow.pyfunc.load_model(f"models:/{NAME}/{version.version}")
    pred = model.predict(pd.DataFrame({"lag_1": current["amount_usd"].astype(float)}))

    run_id = digest(
        cutoff.date(),
        target_week.date(),
        NAME,
        version.version,
        fingerprint,
    )[:24]

    rows = []
    for source, forecast in zip(
        current.itertuples(index=False),
        pred.itertuples(index=False),
    ):
        forecast_id = digest(
            source.advertiser_id,
            target_week.date(),
            cutoff.date(),
            NAME,
            version.version,
            fingerprint,
        )
        rows.append(
            {
                "forecast_id": forecast_id,
                "run_id": run_id,
                "advertiser_id": source.advertiser_id,
                "data_cutoff_week": cutoff.date(),
                "target_week": target_week.date(),
                "model_name": NAME,
                "model_version": str(version.version),
                "model_run_id": version.run_id,
                "source_fingerprint": fingerprint,
                "lag_1": float(source.amount_usd),
                "point": float(forecast.point),
                "p10": float(forecast.p10),
                "p50": float(forecast.p50),
                "p90": float(forecast.p90),
                "run_mode": mode,
            }
        )

    batch = pd.DataFrame(rows)
    if len(batch) != len(current) or batch["forecast_id"].duplicated().any():
        raise RuntimeError("Forecast count/key không hợp lệ")
    if not ((batch["p10"] <= batch["p50"]) & (batch["p50"] <= batch["p90"])).all():
        raise RuntimeError("Quantile crossing")

    summary = {
        "run_id": run_id,
        "model_version": version.version,
        "data_cutoff_week": cutoff.date().isoformat(),
        "target_week": target_week.date().isoformat(),
        "source_fingerprint": fingerprint,
        "cohort_size": len(cohort),
        "scored_count": len(batch),
        "abstained_count": abstained,
        "total_point_usd": round(batch["point"].sum(), 2),
        "run_mode": mode,
        "execute": args.execute,
    }
    print(json.dumps(summary, indent=2))

    if not args.execute:
        print("DRY_RUN: chưa ghi forecast mart")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(FORECAST_DB)) as con:
        con.execute(SCHEMA_SQL.read_text())
        con.register("score_batch", batch)

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("""
                MERGE INTO serving.forecast_history AS target
                USING score_batch AS source
                ON target.forecast_id = source.forecast_id
                WHEN NOT MATCHED THEN INSERT (
                    forecast_id, run_id, advertiser_id,
                    data_cutoff_week, target_week,
                    model_name, model_version, model_run_id,
                    source_fingerprint, lag_1,
                    point, p10, p50, p90, run_mode
                ) VALUES (
                    source.forecast_id, source.run_id, source.advertiser_id,
                    source.data_cutoff_week, source.target_week,
                    source.model_name, source.model_version,
                    source.model_run_id, source.source_fingerprint,
                    source.lag_1, source.point,
                    source.p10, source.p50, source.p90, source.run_mode
                )
            """)

            con.execute(
                """
                INSERT INTO serving.forecast_runs (
                    run_id, data_cutoff_week, target_week,
                    model_name, model_version, model_run_id,
                    source_fingerprint, cohort_size, scored_count,
                    abstained_count, run_mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (run_id) DO NOTHING
            """,
                [
                    run_id,
                    cutoff.date(),
                    target_week.date(),
                    NAME,
                    str(version.version),
                    version.run_id,
                    fingerprint,
                    len(cohort),
                    len(batch),
                    abstained,
                    mode,
                ],
            )

            stored = con.execute(
                """
                SELECT COUNT(*)
                FROM serving.forecast_history
                WHERE run_id = ?
            """,
                [run_id],
            ).fetchone()[0]
            if stored != len(batch):
                raise RuntimeError(f"Mart có {stored} rows, batch có {len(batch)}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    print(f"PUBLISHED rows={len(batch)} db={FORECAST_DB}")


if __name__ == "__main__":
    main()
