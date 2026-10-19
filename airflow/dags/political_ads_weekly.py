import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import DAG, get_current_context, task

ROOT = Path(__file__).resolve().parents[2]
PROJECT_PYTHON = ROOT / ".venv/bin/python"
FLOW = ROOT / "scripts/week7_flow.py"


def run_step(action: str, selected: dict, fail_once=False) -> None:
    command = [
        str(PROJECT_PYTHON),
        str(FLOW),
        action,
        "--logical-date",
        selected["logical_date"],
        "--week-start",
        selected["week_start"],
        "--source-run-id",
        selected["source_run_id"],
    ]

    if fail_once:
        command.append("--fail-once")

    subprocess.run(command, cwd=ROOT, check=True)


with DAG(
    dag_id="political_ads_weekly",
    start_date=pendulum.datetime(2026, 9, 21, tz="UTC"),
    schedule="0 0 * * 1",
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        "owner": "data-engineering",
        "retries": 1,
        "retry_delay": timedelta(seconds=30),
    },
    tags=["political-ads", "local", "week7"],
):

    @task(execution_timeout=timedelta(minutes=5))
    def extract_task() -> dict:
        logical_date = get_current_context()["logical_date"].date().isoformat()

        output = subprocess.check_output(
            [
                str(PROJECT_PYTHON),
                str(FLOW),
                "extract",
                "--logical-date",
                logical_date,
            ],
            cwd=ROOT,
            text=True,
        )

        selected = json.loads(output.strip())
        print(selected)
        return selected

    @task(execution_timeout=timedelta(minutes=10))
    def load_task(selected: dict) -> dict:
        run_step("load", selected)
        return selected

    @task(execution_timeout=timedelta(minutes=20))
    def transform_task(selected: dict) -> dict:
        conf = get_current_context()["dag_run"].conf or {}
        run_step(
            "transform",
            selected,
            fail_once=conf.get("fail_once") == "transform",
        )
        return selected

    @task(execution_timeout=timedelta(minutes=5))
    def quality_task(selected: dict) -> dict:
        run_step("quality", selected)
        return selected

    @task(execution_timeout=timedelta(minutes=5))
    def publish_task(selected: dict) -> None:
        run_step("publish", selected)

    publish_task(quality_task(transform_task(load_task(extract_task()))))
