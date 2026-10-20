"""Validate Week 11 out-of-fold results and build portfolio-safe charts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from mlflow import get_experiment_by_name, set_tracking_uri
from mlflow.tracking import MlflowClient

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "data/tmp/week11"
REPORT = ROOT / "reports/week11"
CHARTS = REPORT / "charts"
DB = ROOT / "data/tmp/week4/warehouse.duckdb"
SQL = ROOT / "sql/week11/features.sql"
MODELS = ("naive_last_week", "lightgbm_point_quantile")
COLORS = {MODELS[0]: "#2463a5", MODELS[1]: "#e58130"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(manifest: dict, predictions: pd.DataFrame) -> None:
    if manifest["warehouse_sha256"] != sha256(DB):
        raise ValueError("Gold changed after training")
    if manifest["sql_sha256"] != sha256(SQL):
        raise ValueError("Feature SQL changed after training")
    if len(manifest["folds"]) != 4 or manifest["weeks_available"] < 20:
        raise ValueError("Need four folds and at least 20 source weeks")

    keys = ["fold", "advertiser_id", "week_start_date"]
    for model in MODELS:
        part = predictions[predictions["model"] == model]
        if part.duplicated(keys).any():
            raise ValueError(f"Duplicate test key: {model}")
        if set(part["fold"]) != {1, 2, 3, 4}:
            raise ValueError(f"Missing fold: {model}")

    naive = predictions[predictions["model"] == MODELS[0]]
    lightgbm = predictions[predictions["model"] == MODELS[1]]
    merged = naive.merge(lightgbm, on=keys, how="outer", validate="one_to_one")
    if len(merged) != len(naive) or len(merged) != len(lightgbm):
        raise ValueError("Models do not use identical test keys")
    if not np.allclose(merged["actual_x"], merged["actual_y"]):
        raise ValueError("Models do not use identical actual values")

    for model in MODELS:
        part = predictions[predictions["model"] == model]
        if not ((part["p10"] <= part["p50"]) & (part["p50"] <= part["p90"])).all():
            raise ValueError(f"Crossed output quantiles: {model}")
        forecast_date = pd.to_datetime(part["week_start_date"])
        origin_date = pd.to_datetime(part["forecast_origin_date"])
        if not ((forecast_date - origin_date).dt.days == 7).all():
            raise ValueError(f"Invalid one-week forecast origin: {model}")

    for fold in manifest["folds"]:
        part = naive[naive["fold"] == fold["fold"]]
        dates = set(pd.to_datetime(part["week_start_date"]).dt.date.astype(str))
        if dates != set(fold["test_weeks"]) or len(part) != fold["test_rows"]:
            raise ValueError(f"Fold {fold['fold']} does not match manifest")
        if fold["train_end"] >= min(fold["test_weeks"]):
            raise ValueError(f"Train overlaps test: fold {fold['fold']}")


def save(fig, name: str) -> None:
    fig.savefig(CHARTS / name, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_charts(
    leaderboard: pd.DataFrame, folds: pd.DataFrame, predictions: pd.DataFrame
) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    ordered = leaderboard.set_index("model").loc[list(MODELS)]
    labels = ["Naive", "LightGBM"]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, column, title in zip(
        axes,
        ["mae", "wape"],
        ["MAE · USD per advertiser-week", "WAPE · lower is better"],
        strict=True,
    ):
        values = ordered[column].to_numpy()
        bars = ax.bar(labels, values, color=[COLORS[name] for name in MODELS])
        ax.bar_label(bars, fmt="%.2f" if column == "wape" else "%.0f")
        ax.set_title(title)
        ax.set_ylim(0, max(values) * 1.22)
        ax.grid(axis="y", alpha=0.2)
        ax.set_axisbelow(True)
    test_rows = len(predictions[predictions["model"] == MODELS[0]])
    fig.suptitle(f"Week 11 · same {test_rows} out-of-fold rows per model")
    save(fig, "01_leaderboard.png")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for model in MODELS:
        part = folds[folds["model"] == model].sort_values("fold")
        ax.plot(
            part["fold"],
            part["mae"],
            marker="o",
            linewidth=2.5,
            label=labels[MODELS.index(model)],
            color=COLORS[model],
        )
    ax.set(title="MAE changes by temporal fold", xlabel="Fold", ylabel="MAE · USD")
    ax.set_xticks([1, 2, 3, 4])
    ax.grid(alpha=0.2)
    ax.legend()
    save(fig, "02_fold_mae.png")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    coverages = ordered["coverage_80"].to_numpy() * 100
    bars = ax.bar(labels, coverages, color=[COLORS[name] for name in MODELS])
    ax.bar_label(bars, fmt="%.1f%%")
    ax.axhline(80, color="#555", linestyle="--", label="Nominal 80%")
    ax.set(title="P10–P90 empirical coverage", ylabel="Test rows covered (%)")
    ax.set_ylim(0, 100)
    ax.legend()
    ax.grid(axis="y", alpha=0.2)
    ax.set_axisbelow(True)
    save(fig, "03_interval_coverage.png")

    weekly = predictions.groupby(["model", "week_start_date"], as_index=False)[
        ["actual", "point"]
    ].sum()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for model in MODELS:
        part = weekly[weekly["model"] == model].sort_values("week_start_date")
        dates = pd.to_datetime(part["week_start_date"]).dt.strftime("%m-%d")
        ax.plot(
            dates,
            part["point"] / 1e6,
            marker="o",
            label=labels[MODELS.index(model)],
            color=COLORS[model],
        )
    actual = weekly[weekly["model"] == MODELS[0]].sort_values("week_start_date")
    dates = pd.to_datetime(actual["week_start_date"]).dt.strftime("%m-%d")
    ax.plot(
        dates,
        actual["actual"] / 1e6,
        marker="s",
        linewidth=2.5,
        color="#242424",
        label="Actual",
    )
    ax.set(
        title="Observed vs forecast · evaluated rows only",
        xlabel="Test week",
        ylabel="Spend · USD millions",
    )
    ax.tick_params(axis="x", labelrotation=35)
    ax.grid(alpha=0.2)
    ax.legend()
    save(fig, "04_actual_vs_forecast.png")


def main() -> None:
    manifest = json.loads((TMP / "fold_manifest.json").read_text(encoding="utf-8"))
    leaderboard = pd.read_csv(TMP / "leaderboard.csv")
    folds = pd.read_csv(TMP / "fold_scores.csv")
    predictions = pd.read_parquet(TMP / "predictions.parquet")
    validate(manifest, predictions)
    make_charts(leaderboard, folds, predictions)

    set_tracking_uri(f"sqlite:///{(TMP / 'mlflow.db').resolve()}")
    experiment = get_experiment_by_name("political_ads_week11")
    if experiment is None:
        raise ValueError("MLflow experiment missing")
    runs = MlflowClient().search_runs(
        [experiment.experiment_id],
        filter_string=f"tags.batch_run_id = '{manifest['batch_run_id']}'",
    )
    logged_pairs = {
        (run.data.tags.get("model"), run.data.tags.get("fold")) for run in runs
    }
    expected_pairs = {(model, str(fold)) for model in MODELS for fold in range(1, 5)}
    if logged_pairs != expected_pairs or len(runs) != 8:
        raise ValueError("MLflow does not contain eight matching model-fold runs")

    REPORT.mkdir(parents=True, exist_ok=True)
    evidence = {
        "status": "PASS_EXPERIMENTAL_BACKTEST",
        "batch_run_id": manifest["batch_run_id"],
        "mlflow_runs": len(runs),
        "warehouse_sha256": manifest["warehouse_sha256"],
        "feature_sql_sha256": manifest["sql_sha256"],
        "predictions_sha256": sha256(TMP / "predictions.parquet"),
        "scope": manifest["target"],
        "evaluation_protocol": manifest["evaluation_protocol"],
        "weeks_available": manifest["weeks_available"],
        "cohort_advertisers": manifest["cohort_advertisers"],
        "same_test_keys": True,
        "missing_week_policy": manifest["missing_week_policy"],
        "folds": manifest["folds"],
        "leaderboard": leaderboard.to_dict(orient="records"),
        "charts": sorted(path.name for path in CHARTS.glob("*.png")),
    }
    (REPORT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("Validated 4 folds, same test keys, no duplicate predictions")
    print(leaderboard.to_string(index=False))


if __name__ == "__main__":
    main()
