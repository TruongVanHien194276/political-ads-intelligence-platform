from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FORECAST_DB = ROOT / "data/tmp/week13/forecast.duckdb"
GOLD_DB = ROOT / "data/tmp/week4/warehouse.duckdb"
SCHEMA_SQL = ROOT / "sql/week13/01_forecast_mart.sql"


def population_stability_index(reference: np.ndarray, current: np.ndarray) -> float:
    """PSI trên log-spend, 5 bins từ reference; epsilon tránh log(0)."""
    if len(reference) < 30 or len(current) < 30:
        raise ValueError("Cần ít nhất 30 điểm cho mỗi phân phối drift")
    reference = np.log1p(reference)
    current = np.log1p(current)
    edges = np.unique(np.quantile(reference, [0.2, 0.4, 0.6, 0.8]))
    ref_counts = np.histogram(reference, bins=[-np.inf, *edges, np.inf])[0]
    cur_counts = np.histogram(current, bins=[-np.inf, *edges, np.inf])[0]
    ref_share = (ref_counts + 0.5) / (len(reference) + 0.5 * len(ref_counts))
    cur_share = (cur_counts + 0.5) / (len(current) + 0.5 * len(cur_counts))
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


def monitor(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(SCHEMA_SQL.read_text())
    escaped = str(GOLD_DB).replace("'", "''")
    con.execute(f"ATTACH '{escaped}' AS gold (READ_ONLY)")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE observed_evaluations AS
        SELECT f.forecast_id, g.source_row_hash AS actual_source_row_hash,
            CAST(g.amount_usd AS DOUBLE) AS actual,
            ABS(CAST(g.amount_usd AS DOUBLE) - f.point) AS absolute_error,
            CAST(g.amount_usd AS DOUBLE) BETWEEN f.p10 AND f.p90 AS covered_80,
            CAST(g.amount_usd AS DOUBLE) > GREATEST(f.p90, f.point * 1.5)
                AND CAST(g.amount_usd AS DOUBLE) - f.point > 10000 AS spike,
            f.point, f.p90
        FROM serving.forecast_history AS f
        JOIN gold.main.fct_advertiser_weekly_spend AS g
          ON g.advertiser_id = f.advertiser_id
         AND g.week_start_date = f.target_week
    """)

    con.execute("BEGIN TRANSACTION")
    try:
        # Hash actual giữ lịch sử source correction; rerun cùng hash là no-op
        con.execute("""
            MERGE INTO serving.forecast_evaluations AS t
            USING observed_evaluations AS s
            ON t.forecast_id = s.forecast_id
               AND t.actual_source_row_hash = s.actual_source_row_hash
            WHEN NOT MATCHED THEN INSERT (
                forecast_id, actual_source_row_hash, actual,
                absolute_error, covered_80, spike
            ) VALUES (
                s.forecast_id, s.actual_source_row_hash, s.actual,
                s.absolute_error, s.covered_80, s.spike
            )
        """)
        con.execute("""
            MERGE INTO serving.forecast_alerts AS t
            USING (
                SELECT SHA256(forecast_id || '|' || actual_source_row_hash
                    || '|SPIKE') AS alert_id, forecast_id,
                    actual_source_row_hash, 'SPIKE' AS alert_type,
                    actual, point, p90
                FROM observed_evaluations WHERE spike
            ) AS s ON t.alert_id = s.alert_id
            WHEN NOT MATCHED THEN INSERT (
                alert_id, forecast_id, actual_source_row_hash,
                alert_type, actual, point, p90
            ) VALUES (
                s.alert_id, s.forecast_id, s.actual_source_row_hash,
                s.alert_type, s.actual, s.point, s.p90
            )
        """)

        drift = []
        runs = con.execute("""
            SELECT run_id, data_cutoff_week
            FROM serving.forecast_runs ORDER BY data_cutoff_week
        """).fetchall()
        for run_id, cutoff in runs:
            current = np.array(
                [
                    row[0]
                    for row in con.execute(
                        """
                SELECT lag_1 FROM serving.forecast_history WHERE run_id = ?
            """,
                        [run_id],
                    ).fetchall()
                ],
                dtype=float,
            )
            reference = np.array(
                [
                    row[0]
                    for row in con.execute(
                        """
                SELECT CAST(g.amount_usd AS DOUBLE)
                FROM gold.main.fct_advertiser_weekly_spend AS g
                JOIN (SELECT DISTINCT advertiser_id
                      FROM serving.forecast_history WHERE run_id = ?) AS cohort
                USING (advertiser_id)
                WHERE g.week_start_date >= ? - INTERVAL 8 WEEK
                  AND g.week_start_date < ?
            """,
                        [run_id, cutoff, cutoff],
                    ).fetchall()
                ],
                dtype=float,
            )
            if len(current) >= 30 and len(reference) >= 30:
                psi = population_stability_index(reference, current)
                status = "DRIFT" if psi >= 0.2 else "OK"
            else:
                psi, status = None, "INSUFFICIENT_DATA"
            con.execute(
                """
                INSERT INTO serving.drift_checks
                    (run_id, input_psi, input_rows, reference_rows, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (run_id) DO UPDATE SET
                    input_psi = excluded.input_psi,
                    input_rows = excluded.input_rows,
                    reference_rows = excluded.reference_rows,
                    status = excluded.status,
                    checked_at_utc = now()
            """,
                [run_id, psi, len(current), len(reference), status],
            )
            drift.append({"run_id": run_id, "psi": psi, "status": status})
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    pending = con.execute("""
        SELECT COUNT(*) FROM serving.forecast_history AS f
        WHERE f.run_mode = 'LIVE' AND NOT EXISTS (
            SELECT 1 FROM serving.forecast_evaluations AS e
            WHERE e.forecast_id = f.forecast_id
        )
    """).fetchone()[0]
    metrics = con.execute("""
        WITH current_actual AS (
            SELECT e.*
            FROM serving.forecast_evaluations AS e
            JOIN serving.forecast_history AS f
              ON f.forecast_id = e.forecast_id
            JOIN gold.main.fct_advertiser_weekly_spend AS g
              ON g.advertiser_id = f.advertiser_id
             AND g.week_start_date = f.target_week
             AND g.source_row_hash = e.actual_source_row_hash
        )
        SELECT COUNT(*), AVG(absolute_error),
            SUM(absolute_error) / NULLIF(SUM(ABS(actual)), 0),
            AVG(CAST(covered_80 AS DOUBLE))
        FROM current_actual
    """).fetchone()
    return {
        "pending_live": pending,
        "evaluated": metrics[0],
        "mae": metrics[1],
        "wape": metrics[2],
        "coverage_80": metrics[3],
        "spike_alerts": con.execute(
            "SELECT COUNT(*) FROM serving.forecast_alerts"
        ).fetchone()[0],
        "drift": drift,
    }


def main() -> None:
    if not FORECAST_DB.exists():
        raise FileNotFoundError("Chưa có forecast mart; chạy week13_score.py trước")
    with duckdb.connect(str(FORECAST_DB)) as con:
        print(json.dumps(monitor(con), indent=2, default=str))


if __name__ == "__main__":
    main()
