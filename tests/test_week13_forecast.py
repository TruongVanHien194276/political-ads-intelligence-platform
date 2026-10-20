from pathlib import Path

import duckdb
import numpy as np
import pytest

from scripts import week13_monitor
from scripts.week13_monitor import population_stability_index

ROOT = Path(__file__).resolve().parents[1]


def test_psi_identical_distribution_is_zero():
    values = np.linspace(0, 100, 100)
    assert population_stability_index(values, values) == pytest.approx(0)


def test_psi_detects_shift():
    baseline = np.linspace(1, 100, 100)
    shifted = baseline + 1000
    assert population_stability_index(baseline, shifted) >= 0.2


def test_psi_requires_enough_rows():
    with pytest.raises(ValueError, match="30"):
        population_stability_index(np.array([1.0]), np.array([2.0]))


def test_forecast_schema_and_current_view():
    with duckdb.connect(":memory:") as con:
        con.execute((ROOT / "sql/week13/01_forecast_mart.sql").read_text())
        con.execute("""
            INSERT INTO serving.forecast_history (
                forecast_id, run_id, advertiser_id, data_cutoff_week,
                target_week, model_name, model_version, model_run_id,
                source_fingerprint, lag_1, point, p10, p50, p90, run_mode
            ) VALUES
            ('a', 'r', 'adv', '2026-10-04', '2026-10-11', 'm', '1', 'ml1',
             'hash1', 100, 100, 80, 100, 120, 'RETROSPECTIVE'),
            ('b', 'r2', 'adv', '2026-10-04', '2026-10-11', 'm', '2', 'ml2',
             'hash2', 100, 100, 80, 100, 120, 'RETROSPECTIVE')
        """)
        assert (
            con.execute("SELECT COUNT(*) FROM serving.forecast_current").fetchone()[0]
            == 1
        )
        with pytest.raises(duckdb.ConstraintException):
            con.execute("""
                INSERT INTO serving.forecast_history
                SELECT * FROM serving.forecast_history WHERE forecast_id = 'a'
            """)


def test_monitor_source_correction_preserves_history(tmp_path, monkeypatch):
    gold = tmp_path / "gold.duckdb"
    forecast = tmp_path / "forecast.duckdb"
    with duckdb.connect(str(gold)) as con:
        con.execute("""
            CREATE TABLE fct_advertiser_weekly_spend (
                advertiser_id VARCHAR, week_start_date DATE,
                amount_usd DOUBLE, source_row_hash VARCHAR
            )
        """)
        con.execute("""
            INSERT INTO fct_advertiser_weekly_spend
            VALUES ('adv', '2026-10-11', 12000, 'source-v1')
        """)
    with duckdb.connect(str(forecast)) as con:
        con.execute((ROOT / "sql/week13/01_forecast_mart.sql").read_text())
        con.execute("""
            INSERT INTO serving.forecast_runs (
                run_id, data_cutoff_week, target_week, model_name,
                model_version, model_run_id, source_fingerprint,
                cohort_size, scored_count, abstained_count, run_mode
            ) VALUES ('r', '2026-10-04', '2026-10-11', 'm', '1',
                      'ml1', 'hash1', 1, 1, 0, 'RETROSPECTIVE')
        """)
        con.execute("""
            INSERT INTO serving.forecast_history (
                forecast_id, run_id, advertiser_id, data_cutoff_week,
                target_week, model_name, model_version, model_run_id,
                source_fingerprint, lag_1, point, p10, p50, p90, run_mode
            ) VALUES ('f', 'r', 'adv', '2026-10-04', '2026-10-11',
                      'm', '1', 'ml1', 'hash1', 10000, 10000,
                      5000, 10000, 20000, 'RETROSPECTIVE')
        """)
    monkeypatch.setattr(week13_monitor, "GOLD_DB", gold)
    for _ in range(2):
        with duckdb.connect(str(forecast)) as con:
            week13_monitor.monitor(con)
    with duckdb.connect(str(forecast), read_only=True) as con:
        assert (
            con.execute("SELECT COUNT(*) FROM serving.forecast_evaluations").fetchone()[
                0
            ]
            == 1
        )
    with duckdb.connect(str(gold)) as con:
        con.execute("""
            UPDATE fct_advertiser_weekly_spend
            SET amount_usd = 40000, source_row_hash = 'source-v2'
        """)
    with duckdb.connect(str(forecast)) as con:
        week13_monitor.monitor(con)
        assert (
            con.execute("SELECT COUNT(*) FROM serving.forecast_evaluations").fetchone()[
                0
            ]
            == 2
        )
        assert (
            con.execute("SELECT COUNT(*) FROM serving.forecast_alerts").fetchone()[0]
            == 1
        )
