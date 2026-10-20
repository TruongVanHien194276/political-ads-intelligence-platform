from __future__ import annotations

import pandas as pd
import pytest

from scripts import week12_common


def test_chronos_context_stops_at_missing_week_and_never_uses_target():
    start = pd.Timestamp("2026-01-04")
    history = pd.DataFrame(
        {
            "advertiser_id": ["A"] * 6,
            "week_start_date": [
                start + pd.Timedelta(weeks=i) for i in (0, 2, 3, 4, 5, 6)
            ],
            "amount_usd": [999.0, 20.0, 30.0, 40.0, 50.0, 1e9],
        }
    )
    context = week12_common.last_contiguous_context(
        history, "A", start + pd.Timedelta(weeks=6)
    )
    assert context.tolist() == [20.0, 30.0, 40.0, 50.0]


def test_exact_test_keys_rejects_missing_and_duplicate(monkeypatch):
    expected = pd.DataFrame(
        {
            "fold": [1, 1],
            "advertiser_id": ["A", "B"],
            "week_start_date": pd.to_datetime(["2026-08-23", "2026-08-23"]),
        }
    )
    monkeypatch.setattr(week12_common, "reference", lambda: expected)

    week12_common.exact_test_keys(expected.copy())
    with pytest.raises(ValueError, match="không khớp"):
        week12_common.exact_test_keys(expected.iloc[:1].copy())
    with pytest.raises(ValueError, match="không khớp"):
        week12_common.exact_test_keys(pd.concat([expected, expected.iloc[:1]]))
