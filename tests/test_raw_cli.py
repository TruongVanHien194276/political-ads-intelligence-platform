from argparse import Namespace
from datetime import date

import pytest

from political_ads.raw_cli import weeks_to_run


def options(**overrides) -> Namespace:
    values = {
        "run_date": None,
        "week_start": None,
        "backfill_start": None,
        "backfill_end": None,
    }
    values.update(overrides)
    return Namespace(**values)


def test_run_date_uses_last_complete_week() -> None:
    assert weeks_to_run(options(run_date=date(2026, 10, 18))) == [date(2026, 10, 11)]


def test_backfill_is_bounded_and_weekly() -> None:
    assert weeks_to_run(
        options(
            backfill_start=date(2026, 9, 20),
            backfill_end=date(2026, 10, 11),
        )
    ) == [
        date(2026, 9, 20),
        date(2026, 9, 27),
        date(2026, 10, 4),
        date(2026, 10, 11),
    ]

    with pytest.raises(ValueError, match="tối đa 4 tuần"):
        weeks_to_run(
            options(
                backfill_start=date(2026, 9, 13),
                backfill_end=date(2026, 10, 11),
            )
        )


def test_non_sunday_week_is_rejected() -> None:
    with pytest.raises(ValueError, match="Chủ nhật"):
        weeks_to_run(options(week_start=date(2026, 10, 5)))
