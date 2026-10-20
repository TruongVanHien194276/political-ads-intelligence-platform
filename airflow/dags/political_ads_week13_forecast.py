"""Local forecast DAG; quality gate is rechecked by week13_register.py."""

import subprocess
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import DAG, get_current_context, task

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "data/tmp/week11/venv/bin/python"

with DAG(
    dag_id="political_ads_week13_forecast",
    start_date=pendulum.datetime(2026, 10, 19, tz="UTC"),
    schedule="30 0 * * 1",
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["political-ads", "forecast", "local"],
):

    @task(execution_timeout=timedelta(minutes=15))
    def score_after_quality() -> None:
        # Gold của Chủ nhật ngay trước logical Monday phải đã publish
        logical_date = get_current_context()["logical_date"].date()
        expected_cutoff = (logical_date - timedelta(days=1)).isoformat()
        subprocess.run(
            [
                str(PYTHON),
                str(ROOT / "scripts/week13_batch.py"),
                "--cutoff",
                expected_cutoff,
            ],
            cwd=ROOT,
            check=True,
        )

    score_after_quality()
