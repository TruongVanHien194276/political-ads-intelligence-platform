from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/tmp/week12"
W11 = ROOT / "data/tmp/week11"
DB = ROOT / "data/tmp/week4/warehouse.duckdb"

KEY = ["fold", "advertiser_id", "week_start_date"]


def reference() -> pd.DataFrame:
    frame = pd.read_parquet(W11 / "predictions.parquet")
    frame["week_start_date"] = pd.to_datetime(frame["week_start_date"])

    # Chỉ naive làm danh sách test chuẩn; không lấy union từ các model
    frame = frame[frame["model"] == "naive_last_week"].copy()
    assert len(frame) == 578, f"Week 11 test rows thay đổi: {len(frame)}"
    assert not frame.duplicated(KEY).any()
    return frame.sort_values(KEY).reset_index(drop=True)


def gold_history() -> pd.DataFrame:
    ref = reference()
    advertiser_ids = ref["advertiser_id"].drop_duplicates().tolist()

    con = duckdb.connect(str(DB), read_only=True)
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 2")

    # Cohort lấy từ test chuẩn; không chọn lại cohort bằng các tuần tương lai
    con.register("cohort_ids", pd.DataFrame({"advertiser_id": advertiser_ids}))
    frame = con.execute("""
        SELECT
            f.advertiser_id,
            f.week_start_date,
            CAST(f.amount_usd AS DOUBLE) AS amount_usd
        FROM fct_advertiser_weekly_spend AS f
        JOIN cohort_ids AS c USING (advertiser_id)
        ORDER BY advertiser_id, week_start_date
    """).df()
    con.close()

    frame["week_start_date"] = pd.to_datetime(frame["week_start_date"])
    assert not frame.duplicated(["advertiser_id", "week_start_date"]).any()
    assert np.isfinite(frame["amount_usd"]).all()
    return frame


def exact_test_keys(candidate: pd.DataFrame) -> None:
    expected = reference()[KEY].sort_values(KEY).reset_index(drop=True)
    actual = candidate[KEY].sort_values(KEY).reset_index(drop=True)

    if candidate.duplicated(KEY).any() or not actual.equals(expected):
        missing = expected.merge(actual, on=KEY, how="left", indicator=True)
        missing = missing[missing["_merge"] == "left_only"]
        raise ValueError(f"Test keys không khớp tuần 11; thiếu {len(missing)} dòng")


def last_contiguous_context(
    history: pd.DataFrame,
    advertiser_id: str,
    target_week: pd.Timestamp,
    max_length: int = 16,
) -> np.ndarray:
    # Chỉ quan sát trước tuần cần dự đoán
    rows = history[
        (history["advertiser_id"] == advertiser_id)
        & (history["week_start_date"] < target_week)
    ].sort_values("week_start_date")

    if rows.empty:
        raise ValueError(f"Không có lịch sử: {advertiser_id}, {target_week}")

    # Đi ngược từ t-1. Gặp tuần thiếu thì dừng, tuyệt đối không đi xuyên gap
    expected_week = target_week - pd.Timedelta(days=7)
    values = []

    for row in rows.iloc[::-1].itertuples(index=False):
        if row.week_start_date != expected_week:
            break
        values.append(float(row.amount_usd))
        expected_week -= pd.Timedelta(days=7)
        if len(values) == max_length:
            break

    if len(values) < 4:
        raise ValueError(f"Context dưới 4 tuần: {advertiser_id}, {target_week}")

    return np.asarray(values[::-1], dtype=np.float32)
