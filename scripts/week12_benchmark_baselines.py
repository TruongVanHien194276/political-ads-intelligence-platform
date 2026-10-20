from __future__ import annotations

import argparse
import json
import resource
import sys
import time

import pandas as pd
from week12_common import OUT, W11

FEATURES = ["lag_1", "lag_2", "lag_4", "rolling_mean_4"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["naive", "lightgbm"], required=True)
    args = parser.parse_args()

    started = time.perf_counter()
    features = pd.read_parquet(W11 / "features.parquet")
    features["week_start_date"] = pd.to_datetime(features["week_start_date"])
    manifest = json.loads((W11 / "fold_manifest.json").read_text())

    training_seconds = 0.0
    inference_seconds = 0.0
    test_rows = 0
    for fold in manifest["folds"]:
        test_start = pd.Timestamp(fold["test_weeks"][0])
        test_end = pd.Timestamp(fold["test_weeks"][-1])
        train = features[features["week_start_date"] < test_start]
        test = features[features["week_start_date"].between(test_start, test_end)]
        test_rows += len(test)

        if args.model == "naive":
            tick = time.perf_counter()
            _ = test["lag_1"].to_numpy()
            residual = train["y"].to_numpy() - train["lag_1"].to_numpy()
            _ = [pd.Series(residual).quantile(q) for q in (0.1, 0.5, 0.9)]
            inference_seconds += time.perf_counter() - tick
            continue

        from week11_train import make_model

        tick = time.perf_counter()
        models = [
            make_model("regression_l1"),
            *(make_model("quantile", q) for q in (0.1, 0.5, 0.9)),
        ]
        for model in models:
            model.fit(train[FEATURES], train["y"].to_numpy())
        training_seconds += time.perf_counter() - tick

        tick = time.perf_counter()
        for model in models:
            _ = model.predict(test[FEATURES])
        inference_seconds += time.perf_counter() - tick

    if test_rows != 578:
        raise ValueError(f"Expected 578 test rows, got {test_rows}")

    result = {
        "model": (
            "naive_last_week" if args.model == "naive" else "lightgbm_point_quantile"
        ),
        "test_rows": test_rows,
        "train_seconds": training_seconds,
        "inference_seconds": inference_seconds,
        "total_process_seconds": time.perf_counter() - started,
        # macOS reports ru_maxrss in bytes
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "python": sys.version.split()[0],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.model}_runtime.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
