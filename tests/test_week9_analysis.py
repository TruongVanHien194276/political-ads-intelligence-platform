import hashlib
import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from scripts.week9_analysis import QUERIES

ROOT = Path(__file__).resolve().parents[1]


def sample_gold():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fct_advertiser_weekly_spend (
            advertiser_id VARCHAR, week_start_date DATE, amount_usd DECIMAL(18, 2)
        )
    """)
    con.execute("""
        INSERT INTO fct_advertiser_weekly_spend VALUES
        ('A', DATE '2026-10-04', 100),
        ('B', DATE '2026-10-04', 50),
        ('A', DATE '2026-10-11', 200),
        ('C', DATE '2026-10-11', 70)
    """)
    con.execute("""
        CREATE TABLE mart_spend_trend_weekly AS
        SELECT DISTINCT week_start_date
        FROM fct_advertiser_weekly_spend
    """)
    con.execute("""
        CREATE TABLE dim_advertiser (
            advertiser_id VARCHAR, advertiser_name VARCHAR
        )
    """)
    con.execute("INSERT INTO dim_advertiser VALUES ('A','A'), ('B','B'), ('C','C')")
    return con


def test_concentration_uses_each_weeks_denominator() -> None:
    con = sample_gold()
    rows = con.execute(QUERIES["02_concentration"]).fetchall()
    assert rows[0][1] == pytest.approx(100.0)
    assert rows[0][2] == pytest.approx(5555.56)
    assert rows[1][1] == pytest.approx(100.0)
    assert rows[1][2] == pytest.approx(6159.12)
    con.close()


def test_movers_do_not_treat_missing_weeks_as_zero() -> None:
    con = sample_gold()
    rows = con.execute(QUERIES["05_movers"]).fetchall()
    by_id = {row[0]: row for row in rows}
    assert by_id["A"][4:6] == ("both_weeks", Decimal("100.00"))
    assert by_id["B"][4:6] == ("not_observed_current", None)
    assert by_id["C"][4:6] == ("newly_observed", None)
    con.close()


def test_committed_evidence_is_valid_and_traces_to_sql() -> None:
    evidence = json.loads((ROOT / "reports/week9/evidence.json").read_text())
    observed_total = sum(
        Decimal(str(row["total_spend_usd"])) for row in evidence["weekly_trend"]
    )
    assert observed_total == Decimal(evidence["metrics"]["fact_total_usd"])
    assert evidence["metrics"]["targeting_ready"] == all(
        row["targeting_metrics_ready"] for row in evidence["targeting_status"]
    )
    cohorts = evidence["mover_cohorts"]
    assert (
        cohorts["both_weeks"] + cohorts["newly_observed"]
        == (evidence["weekly_trend"][-1]["advertiser_count"])
    )
    assert (
        cohorts["both_weeks"] + cohorts["not_observed_current"]
        == (evidence["weekly_trend"][-2]["advertiser_count"])
    )
    assert all(
        0 <= row["top10_share_pct"] <= 100 and 0 <= row["observed_hhi"] <= 10000
        for row in evidence["weekly_concentration"]
    )

    for name, expected_hash in evidence["query_sha256"].items():
        sql = (ROOT / "reports/week9/sql" / f"{name}.sql").read_text()
        assert hashlib.sha256(sql.strip().encode()).hexdigest() == expected_hash

    for index in range(1, 6):
        assert list((ROOT / "reports/week9/charts").glob(f"0{index}_*.png"))
