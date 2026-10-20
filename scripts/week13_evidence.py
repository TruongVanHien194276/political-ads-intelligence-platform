from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import numpy as np
from mlflow.tracking import MlflowClient

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/tmp/week13/forecast.duckdb"
GOLD = ROOT / "data/tmp/week4/warehouse.duckdb"
OUT = ROOT / "reports/week13"
MLFLOW_DB = ROOT / "data/tmp/week11/mlflow.db"
MODEL_NAME = "political_ads_weekly_spend_naive"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    uri = f"sqlite:///{MLFLOW_DB.resolve()}"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    registry = MlflowClient()
    champion = registry.get_model_version_by_alias(MODEL_NAME, "Champion")
    version_count = len(registry.search_model_versions(f"name = '{MODEL_NAME}'"))
    charts = OUT / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB), read_only=True) as con:
        con.execute(f"ATTACH '{str(GOLD)}' AS gold (READ_ONLY)")
        runs = con.execute("""
            SELECT run_id, data_cutoff_week, target_week, model_name,
                model_version, model_run_id, source_fingerprint,
                cohort_size, scored_count, abstained_count, run_mode
            FROM serving.forecast_runs ORDER BY data_cutoff_week
        """).fetchall()
        cols = [x[0] for x in con.description]
        runs = [dict(zip(cols, row)) for row in runs]
        live = con.execute("""
            SELECT advertiser_id, point, p10, p50, p90
            FROM serving.forecast_current
            WHERE run_mode = 'LIVE'
              AND target_week = (
                  SELECT MAX(target_week) FROM serving.forecast_runs
                  WHERE run_mode = 'LIVE'
              )
            ORDER BY point DESC LIMIT 15
        """).fetchall()
        live_totals = con.execute("""
            SELECT COUNT(*), SUM(point)
            FROM serving.forecast_current
            WHERE run_mode = 'LIVE'
              AND target_week = (
                  SELECT MAX(target_week) FROM serving.forecast_runs
                  WHERE run_mode = 'LIVE'
              )
        """).fetchone()
        retro = con.execute("""
            SELECT f.point, f.p10, f.p90, g.amount_usd
            FROM serving.forecast_history AS f
            JOIN gold.main.fct_advertiser_weekly_spend AS g
              ON f.advertiser_id = g.advertiser_id
             AND f.target_week = g.week_start_date
            WHERE f.run_mode = 'RETROSPECTIVE'
        """).fetchall()
        counts = {
            name: con.execute(f"SELECT COUNT(*) FROM serving.{name}").fetchone()[0]
            for name in (
                "forecast_runs",
                "forecast_history",
                "forecast_evaluations",
                "forecast_alerts",
                "drift_checks",
            )
        }
        drift = [
            {"run_id": row[0], "psi": row[1], "status": row[2]}
            for row in con.execute("""
                SELECT run_id, input_psi, status FROM serving.drift_checks
                ORDER BY run_id
            """).fetchall()
        ]
        pending_live = con.execute("""
            SELECT COUNT(*) FROM serving.forecast_history AS f
            WHERE run_mode = 'LIVE' AND NOT EXISTS (
                SELECT 1 FROM serving.forecast_evaluations AS e
                WHERE e.forecast_id = f.forecast_id
            )
        """).fetchone()[0]
        duplicate_keys = con.execute("""
            SELECT COUNT(*) - COUNT(DISTINCT forecast_id)
            FROM serving.forecast_history
        """).fetchone()[0]

    if duplicate_keys or not live or not retro:
        raise RuntimeError("Evidence thiếu dữ liệu hoặc mart có duplicate key")
    if not any(
        row["run_mode"] == "LIVE"
        and row["model_version"] == str(champion.version)
        and row["source_fingerprint"] == champion.tags["source_fingerprint"]
        for row in runs
    ):
        raise RuntimeError("Champion alias không khớp live run")

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(10, 6))
    for n, (advertiser, point, p10, _p50, p90) in enumerate(reversed(live)):
        ax.plot([p10 / 1e6, p90 / 1e6], [n, n], color="#93c5fd", linewidth=5)
        ax.scatter(point / 1e6, n, color="#1d4ed8", s=32, zorder=3)
    ax.set_yticks(range(len(live)), [str(row[0])[:12] for row in reversed(live)])
    ax.set_xlabel("Forecast spend (million USD)")
    ax.set_title("Next-week forecast: top 15 advertisers\nPoint and p10–p90 interval")
    fig.tight_layout()
    fig.savefig(charts / "live_forecast_interval.png", dpi=160)
    plt.close(fig)

    retro_arr = np.array(retro, dtype=float)
    point, low, high, actual = retro_arr.T
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(point / 1e6, actual / 1e6, alpha=0.65, color="#0f766e")
    bound = max(point.max(), actual.max()) / 1e6 * 1.05
    ax.plot([0, bound], [0, bound], linestyle="--", color="#64748b")
    ax.set(
        xlabel="Forecast (million USD)",
        ylabel="Actual (million USD)",
        title="Retrospective 1-week forecast vs actual",
    )
    fig.tight_layout()
    fig.savefig(charts / "retro_actual_vs_forecast.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    residual = (actual - point) / 1000
    ax.hist(residual, bins=20, color="#ea580c", edgecolor="white")
    ax.axvline(0, color="#334155", linestyle="--")
    ax.set(
        xlabel="Actual − forecast (thousand USD)",
        ylabel="Advertiser count",
        title="Retrospective forecast residuals",
    )
    fig.tight_layout()
    fig.savefig(charts / "retro_residuals.png", dpi=160)
    plt.close(fig)

    evidence = {
        "warehouse_sha256": sha256(GOLD),
        "week12_evidence_sha256": sha256(ROOT / "reports/week12/evidence.json"),
        "runs": runs,
        "counts": counts,
        "registry": {
            "champion_version": champion.version,
            "champion_run_id": champion.run_id,
            "registered_versions": version_count,
        },
        "live_latest": {
            "scored_count": live_totals[0],
            "total_point_usd": live_totals[1],
        },
        "duplicate_forecast_ids": duplicate_keys,
        "pending_live_actuals": pending_live,
        "retrospective": {
            "matched_rows": len(retro),
            "mae_usd": float(np.mean(np.abs(actual - point))),
            "wape": float(np.sum(np.abs(actual - point)) / np.sum(np.abs(actual))),
            "coverage_80": float(np.mean((actual >= low) & (actual <= high))),
        },
        "drift": drift,
        "live_target_has_actual": pending_live == 0,
        "charts": [p.name for p in sorted(charts.glob("*.png"))],
    }
    (OUT / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    print(json.dumps(evidence, indent=2, default=str))


if __name__ == "__main__":
    main()
