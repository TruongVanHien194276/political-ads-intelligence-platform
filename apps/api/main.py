from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Lock, Timer
from typing import Annotated, Literal
from uuid import uuid4

import duckdb
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as ApiPath
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]

GOLD_DEFAULT = ROOT / "data/tmp/week4/warehouse.duckdb"
FORECAST_DEFAULT = ROOT / "data/tmp/week13/forecast.duckdb"

# SQL chỉ tồn tại trong server. Client không được truyền tên bảng hoặc ORDER BY
SQL = {
    "overview": """
        SELECT
            b.week_start_date,
            b.total_spend_usd,
            b.advertiser_count,
            b.wow_change_pct,
            (SELECT SUM(total_spend_usd) FROM {bi}) AS loaded_total_spend_usd,
            (SELECT COUNT(*) FROM {advertisers}) AS loaded_advertisers
        FROM {bi} AS b
        WHERE b.week_start_date = COALESCE(
            @week,
            (SELECT MAX(week_start_date) FROM {bi})
        )
    """,
    "trends": """
        SELECT week_start_date, total_spend_usd,
               advertiser_count, wow_change_pct
        FROM {bi}
        WHERE (@from_week IS NULL OR week_start_date >= @from_week)
          AND (@to_week IS NULL OR week_start_date <= @to_week)
        ORDER BY week_start_date DESC
        LIMIT @limit
    """,
    "advertisers_spend": """
        SELECT advertiser_id, advertiser_name, total_spend_usd,
               weeks_observed, last_observed_week,
               share_of_observed_spend_pct, spend_rank
        FROM {advertisers}
        ORDER BY total_spend_usd DESC, advertiser_id
        LIMIT @limit OFFSET @offset
    """,
    "advertisers_name": """
        SELECT advertiser_id, advertiser_name, total_spend_usd,
               weeks_observed, last_observed_week,
               share_of_observed_spend_pct, spend_rank
        FROM {advertisers}
        ORDER BY LOWER(advertiser_name), advertiser_id
        LIMIT @limit OFFSET @offset
    """,
    "advertiser_detail": """
        SELECT advertiser_id, advertiser_name, total_spend_usd,
               weeks_observed, last_observed_week,
               share_of_observed_spend_pct, spend_rank
        FROM {advertisers}
        WHERE advertiser_id = @advertiser_id
    """,
    "forecasts": """
        SELECT
            f.advertiser_id,
            f.data_cutoff_week,
            f.target_week,
            f.point,
            f.p10,
            f.p50,
            f.p90,
            f.model_version,
            f.model_run_id,
            CAST(g.amount_usd AS DOUBLE) AS actual_usd
        FROM {forecast} AS f
        LEFT JOIN {fact} AS g
          ON g.advertiser_id = f.advertiser_id
         AND g.week_start_date = f.target_week
        WHERE f.run_mode = 'LIVE'
          AND f.target_week = COALESCE(
              @target_week,
              (
                  SELECT MAX(target_week)
                  FROM {forecast}
                  WHERE run_mode = 'LIVE'
              )
          )
        ORDER BY f.point DESC, f.advertiser_id
        LIMIT @limit OFFSET @offset
    """,
    "pipeline_health": """
        WITH latest AS (
            SELECT run_id, finished_at_utc, status
            FROM {pipeline_runs}
            ORDER BY finished_at_utc DESC
            LIMIT 1
        )
        SELECT
            l.run_id,
            -- Trả ISO UTC để DuckDB không cần import pytz khi fetch TIMESTAMPTZ
            strftime(
                l.finished_at_utc AT TIME ZONE 'UTC',
                '%Y-%m-%dT%H:%M:%S.%fZ'
            ) AS finished_at_utc,
            l.status,
            (
                SELECT COUNT(*)
                FROM {dq_scorecard} AS d
                WHERE d.run_id = l.run_id
                  AND d.severity = 'BLOCK'
                  AND d.status = 'FAIL'
            ) AS blocking_failures,
            (
                SELECT MAX(week_start_date)
                FROM {fact}
            ) AS gold_latest_week
        FROM latest AS l
    """,
}


@dataclass(frozen=True)
class Settings:
    gold_db: Path
    forecast_db: Path
    cors_origins: tuple[str, ...]
    cache_seconds: int = 30
    query_timeout_seconds: float = 10

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv(
            "API_CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173",
        )

        return cls(
            gold_db=Path(os.getenv("API_GOLD_DB", str(GOLD_DEFAULT))),
            forecast_db=Path(os.getenv("API_FORECAST_DB", str(FORECAST_DEFAULT))),
            cors_origins=tuple(
                origin.strip() for origin in origins.split(",") if origin.strip()
            ),
            cache_seconds=int(os.getenv("API_CACHE_SECONDS", "30")),
            query_timeout_seconds=float(os.getenv("API_QUERY_TIMEOUT_SECONDS", "10")),
        )


class LocalStore:
    TABLES = {
        "bi": "main.mart_bi_weekly",
        "advertisers": "main.mart_api_advertiser",
        "fact": "main.fct_advertiser_weekly_spend",
        "pipeline_runs": "audit.pipeline_runs",
        "dq_scorecard": "audit.dq_scorecard",
        "forecast": "fc.serving.forecast_current",
    }

    def __init__(self, settings: Settings):
        self.settings = settings

    def rows(self, query_name: str, params: dict) -> list[dict]:
        if not self.settings.gold_db.is_file():
            raise HTTPException(
                status_code=503,
                detail="Gold warehouse chưa có trên máy",
            )

        if query_name == "forecasts" and not self.settings.forecast_db.is_file():
            raise HTTPException(
                status_code=503,
                detail="Forecast mart chưa có; chạy make week13",
            )

        # Tên query được chọn từ SQL cố định, không nhận SQL từ request
        sql = SQL[query_name].format(**self.TABLES)
        sql = re.sub(r"@([a-z_]+)", r"$\1", sql)

        with duckdb.connect(
            str(self.settings.gold_db),
            read_only=True,
        ) as con:
            con.execute("SET threads = 2")
            con.execute("SET memory_limit = '512MB'")

            if query_name == "forecasts":
                # DuckDB ATTACH không hỗ trợ bind path bằng dấu ?
                # Path đến từ config server, escape dấu nháy trước khi dùng
                escaped = str(self.settings.forecast_db).replace("'", "''")
                con.execute(f"ATTACH '{escaped}' AS fc (READ_ONLY)")

            # DuckDB không có statement_timeout; interrupt chỉ query trên connection này
            watchdog = Timer(self.settings.query_timeout_seconds, con.interrupt)
            watchdog.start()
            try:
                result = con.execute(sql, params)
                columns = [column[0] for column in result.description]
                return [dict(zip(columns, row)) for row in result.fetchall()]
            except duckdb.InterruptException as exc:
                raise HTTPException(504, "Query vượt thời gian cho phép") from exc
            finally:
                watchdog.cancel()


class TinyCache:
    """Cache trong một process, tối đa 256 query, không cần Redis tuần này"""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self.values: dict[tuple, tuple[float, object]] = {}
        self.lock = Lock()

    def get_or_load(self, key: tuple, loader):
        now = time.monotonic()

        with self.lock:
            entry = self.values.get(key)
            if entry and entry[0] > now:
                return entry[1]

        value = loader()

        with self.lock:
            if len(self.values) >= 256:
                self.values.clear()
            self.values[key] = (now + self.ttl, value)

        return value


class Overview(BaseModel):
    week_start_date: date
    total_spend_usd: float
    advertiser_count: int
    wow_change_pct: float | None
    loaded_total_spend_usd: float
    loaded_advertisers: int


class Trend(BaseModel):
    week_start_date: date
    total_spend_usd: float
    advertiser_count: int
    wow_change_pct: float | None


class Advertiser(BaseModel):
    advertiser_id: str
    advertiser_name: str
    total_spend_usd: float
    weeks_observed: int
    last_observed_week: date
    share_of_observed_spend_pct: float
    spend_rank: int


class AdvertiserPage(BaseModel):
    items: list[Advertiser]
    limit: int
    offset: int
    has_more: bool


class Forecast(BaseModel):
    advertiser_id: str
    data_cutoff_week: date
    target_week: date
    point: float
    p10: float
    p50: float
    p90: float
    model_version: str
    model_run_id: str
    actual_usd: float | None


class ForecastPage(BaseModel):
    items: list[Forecast]
    limit: int
    offset: int
    has_more: bool


class PipelineHealth(BaseModel):
    run_id: str
    finished_at_utc: datetime
    status: str
    blocking_failures: int
    gold_latest_week: date


class GeographyStatus(BaseModel):
    available: bool
    reason: str


def create_app(
    settings: Settings | None = None,
    store: LocalStore | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Political Ads Intelligence API",
        version="0.1.0",
    )

    app.state.store = store or LocalStore(settings)
    app.state.cache = TinyCache(settings.cache_seconds)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["X-Request-ID", "Content-Type"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid4().hex
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    def fetch(request: Request, name: str, **params) -> list[dict]:
        key = (name, tuple(sorted(params.items())))
        return request.app.state.cache.get_or_load(
            key,
            lambda: request.app.state.store.rows(name, params),
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/v1/overview", response_model=Overview)
    def overview(request: Request, week: date | None = None):
        rows = fetch(request, "overview", week=week)
        if not rows:
            raise HTTPException(404, "Tuần này không có trong Gold")
        return rows[0]

    @app.get("/api/v1/trends", response_model=list[Trend])
    def trends(
        request: Request,
        from_week: date | None = None,
        to_week: date | None = None,
        limit: Annotated[int, Query(ge=1, le=104)] = 52,
    ):
        if from_week and to_week and from_week > to_week:
            raise HTTPException(422, "from_week phải <= to_week")

        rows = fetch(
            request,
            "trends",
            from_week=from_week,
            to_week=to_week,
            limit=limit,
        )
        return list(reversed(rows))

    @app.get("/api/v1/advertisers", response_model=AdvertiserPage)
    def advertisers(
        request: Request,
        sort: Literal["spend_desc", "name_asc"] = "spend_desc",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ):
        query_name = "advertisers_spend" if sort == "spend_desc" else "advertisers_name"
        rows = fetch(
            request,
            query_name,
            limit=limit + 1,
            offset=offset,
        )
        return {
            "items": rows[:limit],
            "limit": limit,
            "offset": offset,
            "has_more": len(rows) > limit,
        }

    @app.get(
        "/api/v1/advertisers/{advertiser_id}",
        response_model=Advertiser,
    )
    def advertiser_detail(
        request: Request,
        advertiser_id: Annotated[
            str,
            ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$"),
        ],
    ):
        rows = fetch(
            request,
            "advertiser_detail",
            advertiser_id=advertiser_id,
        )
        if not rows:
            raise HTTPException(404, "Không tìm thấy advertiser")
        return rows[0]

    @app.get("/api/v1/geography", response_model=GeographyStatus)
    def geography():
        return {
            "available": False,
            "reason": (
                "Gold hiện chưa có geo mart; không có số liệu spend theo địa lý để trả"
            ),
        }

    @app.get("/api/v1/forecasts", response_model=ForecastPage)
    def forecasts(
        request: Request,
        target_week: date | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ):
        rows = fetch(
            request,
            "forecasts",
            target_week=target_week,
            limit=limit + 1,
            offset=offset,
        )
        return {
            "items": rows[:limit],
            "limit": limit,
            "offset": offset,
            "has_more": len(rows) > limit,
        }

    @app.get(
        "/api/v1/pipeline-health",
        response_model=PipelineHealth,
    )
    def pipeline_health(request: Request):
        # Không cache health, để thấy lần publish/DQ mới ngay
        rows = request.app.state.store.rows("pipeline_health", {})
        if not rows:
            raise HTTPException(503, "Chưa có pipeline run")
        return rows[0]

    return app


app = create_app()
