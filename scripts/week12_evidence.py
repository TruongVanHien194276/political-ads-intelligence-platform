from __future__ import annotations

import hashlib
import json
import re
import shutil
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import mlflow
import numpy as np
import pandas as pd
from week12_common import DB, KEY, OUT, ROOT, W11, exact_test_keys, reference

REPORT = ROOT / "reports/week12"
MODELS = {
    "naive_last_week",
    "lightgbm_point_quantile",
    "tft_small",
    "chronos_2_small_zero_shot",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timed_model(name: str) -> dict:
    stem = {
        "naive_last_week": "naive",
        "lightgbm_point_quantile": "lightgbm",
        "tft_small": "tft",
        "chronos_2_small_zero_shot": "chronos",
    }[name]
    path = OUT / f"{stem}_runtime.json"
    if path.exists():
        return json.loads(path.read_text())

    if stem != "tft":
        raise FileNotFoundError(path)

    # Recovery for a TFT run started before runtime.json was added
    folds = pd.read_csv(OUT / "tft_runtime.csv")
    log = (OUT / "tft_time.txt").read_text()
    match = re.search(r"(\d+)\s+maximum resident set size", log)
    wall = re.search(r"([\d.]+)\s+real", log)
    if match is None or wall is None:
        raise ValueError("TFT resource log is incomplete")
    return {
        "model": "tft_small",
        "test_rows": int(folds["test_rows"].sum()),
        "train_seconds": float(folds["train_seconds"].sum()),
        "inference_seconds": float(folds["inference_seconds"].sum()),
        "total_process_seconds": float(wall.group(1)),
        "peak_rss_bytes": int(match.group(1)),
        "device": "cpu",
    }


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "charts").mkdir(exist_ok=True)

    leaderboard = pd.read_csv(OUT / "leaderboard.csv")
    predictions = pd.read_parquet(OUT / "all_predictions.parquet")
    predictions["week_start_date"] = pd.to_datetime(predictions["week_start_date"])
    folds = pd.read_csv(OUT / "fold_scores.csv")
    decision = json.loads((OUT / "promotion_decision.json").read_text())
    manifest = json.loads((W11 / "fold_manifest.json").read_text())
    if sha256(DB) != manifest["warehouse_sha256"]:
        raise ValueError("Gold changed after Week 11 fold creation")

    if set(leaderboard["model"]) != MODELS:
        raise ValueError("Leaderboard must contain all four model groups")
    if len(predictions) != 4 * 578 or len(folds) != 4 * 4:
        raise ValueError("Missing test rows or fold scores")

    expected = reference()[KEY + ["actual"]]
    for name, group in predictions.groupby("model"):
        exact_test_keys(group)
        merged = group.merge(expected, on=KEY, suffixes=("", "_ref"))
        if not np.allclose(merged["actual"], merged["actual_ref"], atol=0.01):
            raise ValueError(f"Actual mismatch: {name}")
        if not np.isfinite(group[["point", "p10", "p50", "p90"]]).all().all():
            raise ValueError(f"Nonfinite forecast: {name}")
        if (group["p10"] > group["p50"]).any() or (group["p50"] > group["p90"]).any():
            raise ValueError(f"Quantile crossing: {name}")

    runtime = {name: timed_model(name) for name in sorted(MODELS)}
    for name, item in runtime.items():
        if item["test_rows"] != 578 or item["peak_rss_bytes"] <= 0:
            raise ValueError(f"Invalid runtime evidence: {name}")

    chart_names = ["actual_vs_forecast.png", "residual.png", "interval.png"]
    for name in chart_names:
        shutil.copy2(OUT / "figures" / name, REPORT / "charts" / name)

    batch_id = uuid4().hex
    mlflow.set_tracking_uri(f"sqlite:///{(W11 / 'mlflow.db').resolve()}")
    mlflow.set_experiment("political_ads_week12")
    run_ids = {}
    for row in leaderboard.itertuples(index=False):
        with mlflow.start_run(run_name=f"{row.model}_{batch_id[:8]}") as run:
            run_ids[row.model] = run.info.run_id
            mlflow.set_tags(
                {
                    "batch_run_id": batch_id,
                    "evaluation_protocol": "rolling_one_week_ahead",
                    "source_week11_batch_run_id": manifest["batch_run_id"],
                }
            )
            mlflow.log_params(
                {
                    "model": row.model,
                    "test_rows": 578,
                    "folds": 4,
                    "warehouse_sha256": sha256(DB),
                    "week11_fold_manifest_sha256": sha256(W11 / "fold_manifest.json"),
                }
            )
            if row.model == "chronos_2_small_zero_shot":
                mlflow.log_param("hf_model_id", runtime[row.model]["model_id"])
                mlflow.log_param("hf_revision", runtime[row.model]["model_revision"])
            mlflow.log_metrics(
                {
                    "mae": row.mae,
                    "wape": row.wape,
                    "mase": row.mase,
                    "coverage_80": row.coverage_80,
                    "mean_interval_width_usd": row.mean_interval_width_usd,
                    "pinball_p10": row.pinball_p10,
                    "pinball_p50": row.pinball_p50,
                    "pinball_p90": row.pinball_p90,
                    "train_seconds": runtime[row.model]["train_seconds"],
                    "inference_seconds": runtime[row.model]["inference_seconds"],
                    "peak_rss_mib": runtime[row.model]["peak_rss_bytes"] / 2**20,
                }
            )
            mlflow.log_artifact(str(OUT / "leaderboard.csv"))

    evidence = {
        "status": "PASS_EXPERIMENTAL_BACKTEST",
        "batch_run_id": batch_id,
        "mlflow_run_ids": run_ids,
        "warehouse_sha256": sha256(DB),
        "week11_fold_manifest_sha256": sha256(W11 / "fold_manifest.json"),
        "week11_predictions_sha256": sha256(W11 / "predictions.parquet"),
        "week12_predictions_sha256": sha256(OUT / "all_predictions.parquet"),
        "evaluation_protocol": manifest["evaluation_protocol"],
        "test_rows_per_model": 578,
        "same_test_keys_and_actuals": True,
        "target_leakage_guard_tft": (
            "decoder target perturbation invariant in each fold"
        ),
        "model_promoted": [
            name for name, result in decision.items() if result["promote"]
        ],
        "retained_model": "naive_last_week"
        if not any(result["promote"] for result in decision.values())
        else None,
        "promotion_decision": decision,
        "runtime": runtime,
        "versions": {
            name: version(name)
            for name in (
                "torch",
                "lightning",
                "pytorch-forecasting",
                "chronos-forecasting",
                "mlflow",
            )
        },
        "leaderboard": leaderboard.to_dict(orient="records"),
        "fold_scores": folds.to_dict(orient="records"),
        "charts_sha256": {
            name: sha256(REPORT / "charts" / name) for name in chart_names
        },
    }
    (REPORT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Saved evidence: {REPORT / 'evidence.json'}")


if __name__ == "__main__":
    main()
