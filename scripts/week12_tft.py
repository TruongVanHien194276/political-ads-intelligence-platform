from __future__ import annotations

import json
import resource
import sys
import time

import lightning.pytorch as pl
import numpy as np
import pandas as pd
import torch
from pytorch_forecasting import (
    TemporalFusionTransformer,
    TimeSeriesDataSet,
)
from pytorch_forecasting.data import EncoderNormalizer
from pytorch_forecasting.metrics import QuantileLoss
from week12_common import (
    OUT,
    W11,
    exact_test_keys,
    gold_history,
    reference,
)


def make_segments(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.sort_values(["advertiser_id", "week_start_date"]).copy()

    previous = frame.groupby("advertiser_id")["week_start_date"].shift()
    is_new_segment = previous.isna() | (
        (frame["week_start_date"] - previous).dt.days != 7
    )

    frame["segment_number"] = is_new_segment.groupby(frame["advertiser_id"]).cumsum()
    frame["segment_id"] = (
        frame["advertiser_id"].astype(str) + "_" + frame["segment_number"].astype(str)
    )

    first_week = frame["week_start_date"].min()
    frame["week_idx"] = ((frame["week_start_date"] - first_week).dt.days // 7).astype(
        int
    )
    return frame


def dataset_for_train(frame: pd.DataFrame) -> TimeSeriesDataSet:
    return TimeSeriesDataSet(
        frame,
        time_idx="week_idx",
        target="amount_usd",
        group_ids=["segment_id"],
        min_encoder_length=4,
        max_encoder_length=4,
        min_prediction_length=1,
        max_prediction_length=1,
        time_varying_known_reals=["week_idx"],
        time_varying_unknown_reals=["amount_usd"],
        target_normalizer=EncoderNormalizer(),
        allow_missing_timesteps=False,
        add_relative_time_idx=True,
        add_target_scales=True,
    )


def predict_one(
    model: TemporalFusionTransformer,
    train_dataset: TimeSeriesDataSet,
    segment_rows: pd.DataFrame,
) -> np.ndarray:
    # predict=True chọn đúng cửa sổ cuối: 4 tuần encoder + 1 tuần decoder
    predict_dataset = TimeSeriesDataSet.from_dataset(
        train_dataset,
        segment_rows,
        predict=True,
        stop_randomization=True,
    )
    if len(predict_dataset) != 1:
        raise ValueError(f"Cần đúng 1 forecast window, nhận {len(predict_dataset)}")

    loader = predict_dataset.to_dataloader(
        train=False,
        batch_size=1,
        num_workers=0,
    )
    output = model.predict(
        loader,
        mode="quantiles",
        trainer_kwargs={"accelerator": "cpu", "devices": 1},
    )
    return np.asarray(output.cpu())[0, 0, :]


def main() -> None:
    process_started = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    pl.seed_everything(42, workers=True)
    torch.set_num_threads(2)

    ref = reference()
    panel = make_segments(gold_history())
    manifest = json.loads((W11 / "fold_manifest.json").read_text(encoding="utf-8"))

    predictions = []
    timings = []

    for fold_info in manifest["folds"]:
        fold = fold_info["fold"]
        test_start = pd.Timestamp(fold_info["test_weeks"][0])
        train_panel = panel[panel["week_start_date"] < test_start].copy()

        train_dataset = dataset_for_train(train_panel)
        train_loader = train_dataset.to_dataloader(
            train=True,
            batch_size=32,
            num_workers=0,
        )

        model = TemporalFusionTransformer.from_dataset(
            train_dataset,
            learning_rate=0.01,
            hidden_size=8,
            attention_head_size=1,
            hidden_continuous_size=8,
            dropout=0.1,
            output_size=3,
            loss=QuantileLoss(quantiles=[0.1, 0.5, 0.9]),
            reduce_on_plateau_patience=2,
        )

        trainer = pl.Trainer(
            accelerator="cpu",
            devices=1,
            max_epochs=8,
            logger=False,
            enable_checkpointing=False,
            enable_progress_bar=False,
            gradient_clip_val=0.1,
            limit_train_batches=1.0,
        )

        started = time.perf_counter()
        trainer.fit(model, train_dataloaders=train_loader)
        train_seconds = time.perf_counter() - started

        model.eval()
        fold_ref = ref[ref["fold"] == fold]

        infer_started = time.perf_counter()
        for row_number, row in enumerate(fold_ref.itertuples(index=False)):
            current = panel[
                (panel["advertiser_id"] == row.advertiser_id)
                & (panel["week_start_date"] == row.week_start_date)
            ]
            if len(current) != 1:
                raise ValueError("Không tìm thấy đúng một target Gold row")

            segment_id = current.iloc[0]["segment_id"]
            segment_rows = panel[
                (panel["segment_id"] == segment_id)
                & (panel["week_start_date"] <= row.week_start_date)
            ].copy()

            # Dòng target chỉ dùng làm decoder; các dòng trước là encoder
            q = predict_one(model, train_dataset, segment_rows)
            if row_number == 0:
                # Chứng minh decoder target không đi vào dự báo
                changed = segment_rows.copy()
                changed.loc[
                    changed["week_start_date"] == row.week_start_date,
                    "amount_usd",
                ] = 1e9
                q_changed = predict_one(model, train_dataset, changed)
                if not np.allclose(q, q_changed, rtol=1e-5, atol=0.01):
                    raise ValueError(f"TFT target leakage ở fold {fold}")
            p10, p50, p90 = np.sort(np.maximum(q, 0.0))

            predictions.append(
                {
                    "fold": fold,
                    "advertiser_id": row.advertiser_id,
                    "week_start_date": row.week_start_date,
                    "actual": row.actual,
                    "point": float(p50),
                    "p10": float(p10),
                    "p50": float(p50),
                    "p90": float(p90),
                    "mase_scale": row.mase_scale,
                    "model": "tft_small",
                }
            )

        timings.append(
            {
                "fold": fold,
                "train_seconds": train_seconds,
                "inference_seconds": time.perf_counter() - infer_started,
                "train_windows": len(train_dataset),
                "test_rows": len(fold_ref),
            }
        )
        print(f"fold {fold}: {len(fold_ref)} predictions")

    result = pd.DataFrame(predictions)
    exact_test_keys(result)
    assert np.isfinite(result[["point", "p10", "p50", "p90"]]).all().all()

    result.to_parquet(OUT / "tft_predictions.parquet", index=False)
    pd.DataFrame(timings).to_csv(OUT / "tft_runtime.csv", index=False)
    runtime = {
        "model": "tft_small",
        "test_rows": len(result),
        "train_seconds": sum(row["train_seconds"] for row in timings),
        "inference_seconds": sum(row["inference_seconds"] for row in timings),
        "total_process_seconds": time.perf_counter() - process_started,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "python": sys.version.split()[0],
        "device": "cpu",
    }
    (OUT / "tft_runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
    print("saved TFT predictions and runtime")


if __name__ == "__main__":
    main()
