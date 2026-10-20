from __future__ import annotations

import mlflow.pyfunc
import numpy as np
import pandas as pd


class NaiveSpendModel(mlflow.pyfunc.PythonModel):
    """Point = spend tuần trước; interval = residual quantile từ lịch sử"""

    def __init__(self, residual_q10: float, residual_q50: float, residual_q90: float):
        self.offsets = np.array(
            [residual_q10, residual_q50, residual_q90],
            dtype=float,
        )

    def predict(self, context, model_input: pd.DataFrame, params=None):
        if "lag_1" not in model_input:
            raise ValueError("Thiếu cột lag_1")

        lag = pd.to_numeric(model_input["lag_1"], errors="raise").to_numpy(dtype=float)
        if not np.isfinite(lag).all() or (lag < 0).any():
            raise ValueError("lag_1 phải là spend không âm, hữu hạn")

        # Residual quantile được fit trước tại data cutoff
        interval = np.maximum(lag[:, None] + self.offsets, 0.0)
        interval.sort(axis=1)

        return pd.DataFrame(
            {
                "point": lag,
                "p10": interval[:, 0],
                "p50": interval[:, 1],
                "p90": interval[:, 2],
            }
        )
