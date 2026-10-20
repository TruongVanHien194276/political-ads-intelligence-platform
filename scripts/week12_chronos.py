from __future__ import annotations

import json
import resource
import sys
import time

import numpy as np
import pandas as pd
import torch
from chronos import BaseChronosPipeline
from huggingface_hub import snapshot_download
from week12_common import (
    OUT,
    exact_test_keys,
    gold_history,
    last_contiguous_context,
    reference,
)

MODEL_ID = "autogluon/chronos-2-small"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)

    ref = reference()
    history = gold_history()

    load_started = time.perf_counter()
    pipeline = BaseChronosPipeline.from_pretrained(
        MODEL_ID,
        device_map="cpu",
    )
    load_seconds = time.perf_counter() - load_started
    print(f"model load: {load_seconds:.1f}s")

    rows = []
    inference_started = time.perf_counter()

    # Batch nhỏ để không tạo RAM spike trên máy 8 GB
    for start in range(0, len(ref), 4):
        batch = ref.iloc[start : start + 4]
        contexts = [
            torch.tensor(
                last_contiguous_context(
                    history, row.advertiser_id, row.week_start_date
                ),
                dtype=torch.float32,
            )
            for row in batch.itertuples(index=False)
        ]

        with torch.inference_mode():
            quantiles, means = pipeline.predict_quantiles(
                contexts,
                prediction_length=1,
                quantile_levels=[0.1, 0.5, 0.9],
                batch_size=4,
            )

        for row, q_tensor, mean_tensor in zip(
            batch.itertuples(index=False), quantiles, means
        ):
            q = np.asarray(q_tensor.cpu())[0, 0, :]
            point = float(np.asarray(mean_tensor.cpu())[0, 0])

            # Spend không âm; sau clamp vẫn bảo đảm p10 <= p50 <= p90
            p10, p50, p90 = np.sort(np.maximum(q, 0.0))
            rows.append(
                {
                    "fold": row.fold,
                    "advertiser_id": row.advertiser_id,
                    "week_start_date": row.week_start_date,
                    "actual": row.actual,
                    "point": max(point, 0.0),
                    "p10": float(p10),
                    "p50": float(p50),
                    "p90": float(p90),
                    "mase_scale": row.mase_scale,
                    "model": "chronos_2_small_zero_shot",
                }
            )

        print(f"predicted {min(start + 4, len(ref))}/{len(ref)}")

    result = pd.DataFrame(rows)
    exact_test_keys(result)
    assert np.isfinite(result[["point", "p10", "p50", "p90"]]).all().all()

    result.to_parquet(OUT / "chronos_predictions.parquet", index=False)
    inference_seconds = time.perf_counter() - inference_started
    revision = snapshot_download(MODEL_ID, local_files_only=True).split("/")[-1]
    runtime = {
        "model": "chronos_2_small_zero_shot",
        "model_id": MODEL_ID,
        "model_revision": revision,
        "test_rows": len(result),
        "train_seconds": 0.0,
        "model_load_seconds": load_seconds,
        "inference_seconds": inference_seconds,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "python": sys.version.split()[0],
    }
    (OUT / "chronos_runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
    print(f"inference seconds: {inference_seconds:.1f}")
    print(f"saved: {OUT / 'chronos_predictions.parquet'}")


if __name__ == "__main__":
    main()
