"""Check local hand-offs and measure HTTP latency against a real API process."""

from __future__ import annotations

import hashlib
import json
import socket
import statistics
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import duckdb

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/tmp/week4/warehouse.duckdb"
EVIDENCE = ROOT / "reports/week16/evidence.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def api_benchmark() -> dict:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    command = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "uvicorn",
        "apps.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
    ]
    server = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    url = f"http://127.0.0.1:{port}/api/v1/overview"
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError("API dừng khi khởi động")
            try:
                with urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        break
            except URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError("API không sẵn sàng sau 10 giây")

        samples = []
        for _ in range(50):
            start = time.perf_counter()
            with urlopen(url, timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError(f"API HTTP {response.status}")
                response.read()
            samples.append((time.perf_counter() - start) * 1000)

        rss_kib = int(
            subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(server.pid)], text=True
            ).strip()
        )
        return {
            "endpoint": "/api/v1/overview",
            "requests": len(samples),
            "method": "sequential localhost HTTP, warm cache",
            "p50_ms": round(percentile(samples, 0.5), 2),
            "p95_ms": round(percentile(samples, 0.95), 2),
            "mean_ms": round(statistics.mean(samples), 2),
            "server_rss_mib": round(rss_kib / 1024, 1),
        }
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def main() -> None:
    gold_hash = sha256(GOLD)
    bi = read_json(ROOT / "reports/week10/evidence.json")
    model = read_json(ROOT / "reports/week13/evidence.json")
    api = read_json(ROOT / "reports/week14/evidence.json")
    web = read_json(ROOT / "reports/week15/evidence.json")
    for name, item in {
        "BI snapshot": bi,
        "model": model,
        "API": api,
        "web": web,
    }.items():
        if item["warehouse_sha256"] != gold_hash:
            raise RuntimeError(f"{name} dùng Gold khác; tạo lại evidence trước")
    if not all(check["match"] for check in bi["checks"].values()):
        raise RuntimeError("Workbook local không khớp SQL")
    if not all(check["match"] for check in api["checks"].values()):
        raise RuntimeError("API không khớp SQL")
    for name, check in web["checks"].items():
        if len(set(check.values())) != 1:
            raise RuntimeError(f"Web không khớp SQL/API: {name}")

    with duckdb.connect(str(GOLD), read_only=True) as con:
        rows, weeks, spend, duplicate = con.execute("""
            SELECT COUNT(*), COUNT(DISTINCT week_start_date), SUM(amount_usd),
                   COUNT(*) - COUNT(DISTINCT advertiser_week_key)
            FROM main.fct_advertiser_weekly_spend
        """).fetchone()
        dq_passed, dq_total = con.execute("""
            SELECT COUNT(*) FILTER (WHERE status = 'PASS'), COUNT(*)
            FROM audit.dq_scorecard
            WHERE run_id = (
                SELECT run_id FROM audit.pipeline_runs
                ORDER BY finished_at_utc DESC LIMIT 1
            )
        """).fetchone()
    if duplicate or dq_passed != dq_total or not dq_total:
        raise RuntimeError("Gold hoặc DQ không đạt")

    manifests = list(
        (ROOT / "data/raw").glob("source=*/table=*/week_start=*/run_id=*/manifest.json")
    )
    source_jobs = [read_json(path) for path in manifests]
    result = {
        "status": "PASS_LOCAL_ONLY",
        "gold_sha256": gold_hash,
        "gold": {
            "fact_rows": rows,
            "weeks": weeks,
            "spend_usd": str(spend),
            "duplicate_keys": duplicate,
            "dq_passed": dq_passed,
            "dq_total": dq_total,
        },
        "source_jobs": {
            "count": len(source_jobs),
            "processed_bytes": sum(
                job.get("processed_bytes", 0) for job in source_jobs
            ),
            "billed_bytes": sum(job.get("billed_bytes", 0) for job in source_jobs),
            "note": "Historical BigQuery job stats in local manifests",
        },
        "parity": {
            "sql_excel_snapshot": len(bi["checks"]),
            "sql_api": len(api["checks"]),
            "sql_api_web": len(web["checks"]),
            "power_bi_cloud": "NOT_VERIFIED",
        },
        "api_benchmark": api_benchmark(),
        "cloud_run": "NOT_DEPLOYED_BILLING_OFF",
        "cloud_logs": "NOT_AVAILABLE",
        "cloud_cost_usd": None,
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
