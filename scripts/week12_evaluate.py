from __future__ import annotations

import json

import numpy as np
import pandas as pd
from week12_common import KEY, OUT, W11, exact_test_keys, reference


def metrics(frame: pd.DataFrame) -> dict:
    actual = frame["actual"].to_numpy(float)
    point = frame["point"].to_numpy(float)
    p10 = frame["p10"].to_numpy(float)
    p50 = frame["p50"].to_numpy(float)
    p90 = frame["p90"].to_numpy(float)
    scale = frame["mase_scale"].to_numpy(float)

    error = np.abs(actual - point)

    def pinball(prediction: np.ndarray, alpha: float) -> float:
        diff = actual - prediction
        return float(np.mean(np.maximum(alpha * diff, (alpha - 1) * diff)))

    return {
        "test_rows": len(frame),
        "mae": float(error.mean()),
        "wape": float(error.sum() / np.abs(actual).sum()),
        "mase": float(np.mean(error / scale)),
        "pinball_p10": pinball(p10, 0.1),
        "pinball_p50": pinball(p50, 0.5),
        "pinball_p90": pinball(p90, 0.9),
        "coverage_80": float(np.mean((actual >= p10) & (actual <= p90))),
        "mean_interval_width_usd": float(np.mean(p90 - p10)),
    }


def main() -> None:
    baseline = pd.read_parquet(W11 / "predictions.parquet")
    baseline["week_start_date"] = pd.to_datetime(baseline["week_start_date"])

    candidates = [
        baseline,
        pd.read_parquet(OUT / "chronos_predictions.parquet"),
        pd.read_parquet(OUT / "tft_predictions.parquet"),
    ]
    combined = pd.concat(candidates, ignore_index=True)
    combined["week_start_date"] = pd.to_datetime(combined["week_start_date"])

    expected_actual = reference()[KEY + ["actual"]]
    rows = []
    fold_rows = []

    for model_name, group in combined.groupby("model"):
        exact_test_keys(group)

        check = group.merge(
            expected_actual,
            on=KEY,
            suffixes=("", "_reference"),
            validate="one_to_one",
        )
        if not np.allclose(check["actual"], check["actual_reference"], atol=0.01):
            raise ValueError(f"Actual không khớp Gold: {model_name}")

        if (group["p10"] > group["p50"]).any() or (group["p50"] > group["p90"]).any():
            raise ValueError(f"Quantile crossing: {model_name}")

        rows.append({"model": model_name, **metrics(group)})
        for fold, part in group.groupby("fold"):
            fold_rows.append({"model": model_name, "fold": fold, **metrics(part)})

    leaderboard = pd.DataFrame(rows).sort_values("mae")
    folds = pd.DataFrame(fold_rows)

    leaderboard.to_csv(OUT / "leaderboard.csv", index=False)
    folds.to_csv(OUT / "fold_scores.csv", index=False)
    combined.to_parquet(OUT / "all_predictions.parquet", index=False)

    naive = leaderboard[leaderboard["model"] == "naive_last_week"].iloc[0]
    naive_folds = folds[folds["model"] == "naive_last_week"].set_index("fold")

    decisions = {}
    for row in leaderboard.itertuples(index=False):
        if row.model == "naive_last_week":
            continue

        model_folds = folds[folds["model"] == row.model].set_index("fold")
        fold_ok = bool((model_folds["mae"] <= naive_folds["mae"] * 1.10).all())

        decisions[row.model] = {
            "promote": bool(
                row.mae <= naive.mae * 0.95
                and row.wape <= naive.wape
                and row.coverage_80 >= 0.75
                and row.mean_interval_width_usd <= naive.mean_interval_width_usd * 2
                and fold_ok
            ),
            "all_fold_mae_within_10pct": fold_ok,
        }

    (OUT / "promotion_decision.json").write_text(
        json.dumps(decisions, indent=2) + "\n",
        encoding="utf-8",
    )

    print(leaderboard.to_string(index=False))
    print(json.dumps(decisions, indent=2))


if __name__ == "__main__":
    main()
