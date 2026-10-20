"""Lưu bằng chứng API ↔ Gold local, không gọi BigQuery hay Power BI."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from apps.api.main import FORECAST_DEFAULT, GOLD_DEFAULT, Settings, create_app

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/week14"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if not GOLD_DEFAULT.is_file() or not FORECAST_DEFAULT.is_file():
        raise SystemExit("Thiếu Gold/forecast local; chạy make week6 và make week13")

    # Đọc Gold trực tiếp, không dùng lại logic tính KPI của API
    with duckdb.connect(str(GOLD_DEFAULT), read_only=True) as con:
        sql_spend, sql_advertisers = con.execute("""
            SELECT total_spend_usd, advertiser_count
            FROM mart_bi_weekly
            ORDER BY week_start_date DESC
            LIMIT 1
        """).fetchone()
        sql_loaded_spend = con.execute("""
            SELECT SUM(total_spend_usd) FROM mart_bi_weekly
        """).fetchone()[0]

    settings = Settings(GOLD_DEFAULT, FORECAST_DEFAULT, ())
    app = create_app(settings)
    examples = {}
    paths = {
        "overview": "/api/v1/overview",
        "trends": "/api/v1/trends?limit=3",
        "advertisers": "/api/v1/advertisers?limit=1",
        "geography": "/api/v1/geography",
        "forecasts": "/api/v1/forecasts?limit=1",
        "pipeline_health": "/api/v1/pipeline-health",
    }
    with TestClient(app) as client:
        for name, path in paths.items():
            response = client.get(path)
            if response.status_code != 200:
                raise RuntimeError(
                    f"{path}: HTTP {response.status_code}: {response.text}"
                )
            examples[name] = response.json()

        overview = examples["overview"]
        checks = {
            "latest_week_spend_usd": {
                "sql": float(sql_spend),
                "api": overview["total_spend_usd"],
            },
            "latest_week_advertisers": {
                "sql": sql_advertisers,
                "api": overview["advertiser_count"],
            },
            "loaded_total_spend_usd": {
                "sql": float(sql_loaded_spend),
                "api": overview["loaded_total_spend_usd"],
            },
        }
        for item in checks.values():
            item["match"] = item["sql"] == item["api"]
        if not all(item["match"] for item in checks.values()):
            raise RuntimeError(f"API không khớp Gold: {checks}")

    REPORT.mkdir(parents=True, exist_ok=True)
    evidence = {
        "status": "PASS_LOCAL_API_SQL",
        "power_bi_status": "NOT_VERIFIED",
        "cloud_bigquery_status": "NOT_DEPLOYED_BILLING_OFF",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "warehouse_sha256": sha256(GOLD_DEFAULT),
        "forecast_sha256": sha256(FORECAST_DEFAULT),
        "week_filter": overview["week_start_date"],
        "checks": checks,
        "endpoint_http_status": dict.fromkeys(paths, 200),
        "notes": "Power BI tuần 10 là snapshot cũ; chưa thể xác nhận API = Power BI",
    }
    (REPORT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (REPORT / "examples.json").write_text(
        json.dumps(examples, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (REPORT / "openapi.json").write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"PASS: {len(paths)} endpoints, 3 KPI khớp SQL; {REPORT}")


if __name__ == "__main__":
    main()
