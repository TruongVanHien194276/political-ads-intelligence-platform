from datetime import date, timedelta
from pathlib import Path

import duckdb

SQL = Path(__file__).resolve().parents[1] / "sql/week11/features.sql"


def test_lags_use_only_prior_calendar_weeks_and_missing_stays_missing():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fct_advertiser_weekly_spend (
            advertiser_id VARCHAR,
            week_start_date DATE,
            amount_usd DECIMAL(18, 2)
        )
    """)
    first = date(2026, 5, 31)
    rows = [
        ("A", first + timedelta(weeks=week), 10 + week)
        for week in range(20)
        if week != 10
    ]
    rows += [("B", first + timedelta(weeks=week), 100 + week) for week in range(20)]
    con.executemany("INSERT INTO fct_advertiser_weekly_spend VALUES (?, ?, ?)", rows)

    sql = SQL.read_text(encoding="utf-8")
    week4 = first + timedelta(weeks=4)
    before = con.execute(
        f"SELECT y, lag_1, lag_2, lag_4, rolling_mean_4 "
        f"FROM ({sql}) WHERE advertiser_id = 'A' AND week_start_date = ?",
        [week4],
    ).fetchone()
    assert before == (14.0, 13.0, 12.0, 10.0, 11.5)

    # Thay dữ liệu tương lai không được làm đổi feature của tuần 4
    con.execute(
        "UPDATE fct_advertiser_weekly_spend SET amount_usd = 999 "
        "WHERE advertiser_id = 'A' AND week_start_date = ?",
        [first + timedelta(weeks=6)],
    )
    after = con.execute(
        f"SELECT y, lag_1, lag_2, lag_4, rolling_mean_4 "
        f"FROM ({sql}) WHERE advertiser_id = 'A' AND week_start_date = ?",
        [week4],
    ).fetchone()
    assert after == before

    # A thiếu tuần 10: không có nhãn, tuần 11 cũng thiếu đủ bốn lag liên tiếp
    excluded = con.execute(
        f"SELECT COUNT(*) FROM ({sql}) "
        "WHERE advertiser_id = 'A' AND week_start_date IN (?, ?)",
        [first + timedelta(weeks=10), first + timedelta(weeks=11)],
    ).fetchone()[0]
    assert excluded == 0
