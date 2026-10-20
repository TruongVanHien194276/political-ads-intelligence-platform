from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import duckdb
import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_pinball_loss

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/tmp/week4/warehouse.duckdb"
SQL = ROOT / "sql/week11/features.sql"
OUT = ROOT / "data/tmp/week11"
FEATURES = ["lag_1", "lag_2", "lag_4", "rolling_mean_4"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score(actual, point, p10, p50, p90, mase_scale):
    actual = np.asarray(actual, dtype=float)
    point = np.asarray(point, dtype=float)
    p10 = np.asarray(p10, dtype=float)
    p50 = np.asarray(p50, dtype=float)
    p90 = np.asarray(p90, dtype=float)

    absolute_error = np.abs(actual - point)
    denominator = np.abs(actual).sum()

    return {
        "mae": float(mean_absolute_error(actual, point)),
        "wape": float(absolute_error.sum() / denominator) if denominator > 0 else None,
        # MASE ở đây dùng scale gộp của panel trên train fold
        "mase": float(absolute_error.mean() / mase_scale) if mase_scale > 0 else None,
        "pinball_p10": float(mean_pinball_loss(actual, p10, alpha=0.10)),
        "pinball_p50": float(mean_pinball_loss(actual, p50, alpha=0.50)),
        "pinball_p90": float(mean_pinball_loss(actual, p90, alpha=0.90)),
        "coverage_80": float(np.mean((actual >= p10) & (actual <= p90))),
        "mean_interval_width_usd": float(np.mean(p90 - p10)),
    }


def make_model(objective: str, alpha: float | None = None):
    params = {
        "objective": objective,
        "n_estimators": 150,
        "learning_rate": 0.05,
        "num_leaves": 7,
        "max_depth": 3,
        "min_child_samples": 10,
        "n_jobs": 2,
        "random_state": 42,
        "verbosity": -1,
    }
    if alpha is not None:
        params["alpha"] = alpha
    return lgb.LGBMRegressor(**params)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DB), read_only=True)
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 2")

    warehouse_hash = sha256(DB)
    weeks = [
        row[0]
        for row in con.execute("""
            SELECT DISTINCT week_start_date
            FROM fct_advertiser_weekly_spend
            ORDER BY 1
        """).fetchall()
    ]

    if len(weeks) < 20:
        raise SystemExit(f"BLOCKED_DATA: mới có {len(weeks)} tuần, cần ít nhất 20")

    if any((b - a).days != 7 for a, b in zip(weeks, weeks[1:])):
        raise SystemExit("BLOCKED_DATA: chuỗi tuần không liên tiếp")

    frame = con.execute(SQL.read_text(encoding="utf-8")).df()
    con.close()

    if frame["advertiser_id"].nunique() < 20:
        raise SystemExit("BLOCKED_DATA: cohort đủ 8 tuần đầu có dưới 20 advertiser")

    frame["week_start_date"] = pd.to_datetime(frame["week_start_date"])
    frame.to_parquet(OUT / "features.parquet", index=False)

    db_uri = f"sqlite:///{(OUT / 'mlflow.db').resolve()}"
    mlflow.set_tracking_uri(db_uri)
    mlflow.set_experiment("political_ads_week11")
    batch_run_id = uuid4().hex

    fold_manifest = []
    prediction_rows = []
    fold_scores = []

    # Bốn test folds, mỗi fold hai tuần
    # Tất cả model dùng đúng cùng những dòng test
    for fold_no in range(4):
        start = len(weeks) - 8 + 2 * fold_no
        test_weeks = weeks[start : start + 2]
        test_start = pd.Timestamp(test_weeks[0])
        test_end = pd.Timestamp(test_weeks[-1])

        train = frame[frame["week_start_date"] < test_start].copy()
        test = frame[frame["week_start_date"].between(test_start, test_end)].copy()

        if len(train) < 100 or len(test) == 0:
            raise SystemExit(f"BLOCKED_DATA: fold {fold_no + 1} quá ít mẫu")

        x_train, y_train = train[FEATURES], train["y"].to_numpy()
        x_test, y_test = test[FEATURES], test["y"].to_numpy()

        # Scale của MASE chỉ học từ train, không lấy thông tin test
        train_residual = y_train - train["lag_1"].to_numpy()
        mase_scale = float(np.mean(np.abs(train_residual)))

        fold_manifest.append(
            {
                "fold": fold_no + 1,
                "train_end": train["week_start_date"].max().date().isoformat(),
                "test_weeks": [day.isoformat() for day in test_weeks],
                "train_rows": len(train),
                "test_rows": len(test),
                "test_advertisers": test["advertiser_id"].nunique(),
            }
        )

        # Naive point: tuần sau bằng tuần trước
        naive_point = test["lag_1"].to_numpy()
        naive_p10 = np.maximum(0, naive_point + np.quantile(train_residual, 0.10))
        naive_p50 = np.maximum(0, naive_point + np.quantile(train_residual, 0.50))
        naive_p90 = np.maximum(0, naive_point + np.quantile(train_residual, 0.90))

        point_model = make_model("regression_l1")
        point_model.fit(x_train, y_train)
        lgb_point = np.maximum(0, point_model.predict(x_test))

        quantiles = {}
        for alpha in (0.10, 0.50, 0.90):
            model = make_model("quantile", alpha)
            model.fit(x_train, y_train)
            quantiles[alpha] = np.maximum(0, model.predict(x_test))

        # Sửa quantile crossing; ghi tỷ lệ crossing để kiểm tra model
        raw_quantiles = np.column_stack(
            [quantiles[0.10], quantiles[0.50], quantiles[0.90]]
        )
        crossing_rate = float(
            np.mean(
                (raw_quantiles[:, 0] > raw_quantiles[:, 1])
                | (raw_quantiles[:, 1] > raw_quantiles[:, 2])
            )
        )
        ordered = np.sort(raw_quantiles, axis=1)

        candidates = {
            "naive_last_week": (naive_point, naive_p10, naive_p50, naive_p90),
            "lightgbm_point_quantile": (
                lgb_point,
                ordered[:, 0],
                ordered[:, 1],
                ordered[:, 2],
            ),
        }

        for name, (point, p10, p50, p90) in candidates.items():
            metrics = score(y_test, point, p10, p50, p90, mase_scale)
            fold_scores.append(
                {
                    "fold": fold_no + 1,
                    "model": name,
                    "crossing_rate": crossing_rate
                    if name.startswith("lightgbm")
                    else 0,
                    **metrics,
                }
            )

            for index, row in enumerate(test.itertuples(index=False)):
                prediction_rows.append(
                    {
                        "fold": fold_no + 1,
                        "model": name,
                        "advertiser_id": row.advertiser_id,
                        "week_start_date": row.week_start_date,
                        "forecast_origin_date": row.week_start_date - timedelta(days=7),
                        "actual": float(y_test[index]),
                        "point": float(point[index]),
                        "p10": float(p10[index]),
                        "p50": float(p50[index]),
                        "p90": float(p90[index]),
                        "mase_scale": mase_scale,
                    }
                )

            with mlflow.start_run(run_name=f"{name}_fold_{fold_no + 1}"):
                mlflow.set_tags(
                    {
                        "batch_run_id": batch_run_id,
                        "model": name,
                        "fold": str(fold_no + 1),
                    }
                )
                mlflow.log_params(
                    {
                        "model": name,
                        "fold": fold_no + 1,
                        "data_sha256": warehouse_hash,
                        "test_weeks": ",".join(day.isoformat() for day in test_weeks),
                    }
                )
                mlflow.log_metrics(
                    {key: value for key, value in metrics.items() if value is not None}
                )

    pd.DataFrame(prediction_rows).to_parquet(OUT / "predictions.parquet", index=False)
    pd.DataFrame(fold_scores).to_csv(OUT / "fold_scores.csv", index=False)

    leaderboard = []
    predictions = pd.DataFrame(prediction_rows)

    # Tính lại trên toàn bộ out-of-fold rows thay vì lấy trung bình WAPE fold
    for name, group in predictions.groupby("model"):
        values = score(
            group["actual"],
            group["point"],
            group["p10"],
            group["p50"],
            group["p90"],
            mase_scale=1.0,
        )
        values["mase"] = float(
            np.mean(np.abs(group["actual"] - group["point"]) / group["mase_scale"])
        )
        leaderboard.append(
            {
                "model": name,
                "test_rows": len(group),
                **values,
            }
        )

    leaderboard = pd.DataFrame(leaderboard).sort_values("mae")
    leaderboard.to_csv(OUT / "leaderboard.csv", index=False)

    manifest = {
        "batch_run_id": batch_run_id,
        "warehouse_sha256": warehouse_hash,
        "sql_sha256": sha256(SQL),
        "weeks_available": len(weeks),
        "cohort_advertisers": int(frame["advertiser_id"].nunique()),
        "target": "observed advertiser-week USD spend",
        "missing_week_policy": "NULL, never zero",
        "evaluation_protocol": (
            "Rolling one-week-ahead: actual from first test week may be used "
            "as a lag for the second test week; model weights remain fixed "
            "within each two-week fold"
        ),
        "folds": fold_manifest,
    }
    (OUT / "fold_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(leaderboard.to_string(index=False))
    print(f"Artifacts: {OUT}")


if __name__ == "__main__":
    main()
