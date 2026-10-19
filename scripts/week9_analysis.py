from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data/tmp/week4/warehouse.duckdb"
OUT = ROOT / "data/tmp/week9"
SQL_OUT = ROOT / "reports/week9/sql"
CHART_OUT = ROOT / "reports/week9/charts"
EVIDENCE_OUT = ROOT / "reports/week9/evidence.json"


QUERIES = {
    "01_trend": """
        SELECT
            week_start_date,
            total_spend_usd,
            advertiser_count,
            previous_spend_usd,
            wow_change_pct
        FROM mart_spend_trend_weekly
        ORDER BY week_start_date
    """,
    "02_concentration": """
        WITH ranked AS (
            SELECT
                week_start_date,
                advertiser_id,
                amount_usd,
                SUM(amount_usd) OVER (
                    PARTITION BY week_start_date
                ) AS week_total_usd,
                ROW_NUMBER() OVER (
                    PARTITION BY week_start_date
                    ORDER BY amount_usd DESC, advertiser_id
                ) AS spend_rank
            FROM fct_advertiser_weekly_spend
        )
        SELECT
            week_start_date,
            ROUND(
                100.0 * SUM(
                    CASE WHEN spend_rank <= 10 THEN amount_usd ELSE 0 END
                ) / NULLIF(MAX(week_total_usd), 0),
                2
            ) AS top10_share_pct,
            ROUND(
                10000.0 * SUM(
                    POWER(amount_usd / NULLIF(week_total_usd, 0), 2)
                ),
                2
            ) AS observed_hhi
        FROM ranked
        GROUP BY week_start_date
        ORDER BY week_start_date
    """,
    "03_distribution": """
        WITH bucketed AS (
            SELECT
                week_start_date,
                CASE
                    WHEN amount_usd < 1000 THEN 1
                    WHEN amount_usd < 10000 THEN 2
                    WHEN amount_usd < 100000 THEN 3
                    WHEN amount_usd < 1000000 THEN 4
                    ELSE 5
                END AS bucket_order,
                CASE
                    WHEN amount_usd < 1000 THEN '< 1k USD'
                    WHEN amount_usd < 10000 THEN '1k–10k USD'
                    WHEN amount_usd < 100000 THEN '10k–100k USD'
                    WHEN amount_usd < 1000000 THEN '100k–1m USD'
                    ELSE '≥ 1m USD'
                END AS spend_bucket
            FROM fct_advertiser_weekly_spend
        )
        SELECT
            week_start_date,
            bucket_order,
            spend_bucket,
            COUNT(*) AS advertiser_count
        FROM bucketed
        GROUP BY week_start_date, bucket_order, spend_bucket
        ORDER BY week_start_date, bucket_order
    """,
    "04_distribution_summary": """
        SELECT
            week_start_date,
            COUNT(*) AS advertiser_count,
            MEDIAN(amount_usd) AS median_spend_usd,
            QUANTILE_CONT(amount_usd, 0.90) AS p90_spend_usd,
            QUANTILE_CONT(amount_usd, 0.99) AS p99_spend_usd,
            MAX(amount_usd) AS max_spend_usd
        FROM fct_advertiser_weekly_spend
        GROUP BY week_start_date
        ORDER BY week_start_date
    """,
    "05_movers": """
        WITH periods AS (
            SELECT
                MAX(week_start_date) AS current_week,
                MAX(week_start_date) - 7 AS previous_week
            FROM mart_spend_trend_weekly
        ),
        paired AS (
            SELECT
                f.advertiser_id,
                MAX(f.amount_usd) FILTER (
                    WHERE f.week_start_date = p.previous_week
                ) AS previous_spend_usd,
                MAX(f.amount_usd) FILTER (
                    WHERE f.week_start_date = p.current_week
                ) AS current_spend_usd
            FROM fct_advertiser_weekly_spend AS f
            CROSS JOIN periods AS p
            WHERE f.week_start_date IN (
                p.previous_week, p.current_week
            )
            GROUP BY f.advertiser_id
        )
        SELECT
            x.advertiser_id,
            d.advertiser_name,
            x.previous_spend_usd,
            x.current_spend_usd,
            CASE
                WHEN x.previous_spend_usd IS NOT NULL
                 AND x.current_spend_usd IS NOT NULL
                    THEN 'both_weeks'
                WHEN x.previous_spend_usd IS NULL
                    THEN 'newly_observed'
                ELSE 'not_observed_current'
            END AS cohort,
            CASE
                -- Chỉ tính thay đổi khi có quan sát ở cả hai tuần
                WHEN x.previous_spend_usd IS NOT NULL
                 AND x.current_spend_usd IS NOT NULL
                    THEN x.current_spend_usd - x.previous_spend_usd
                ELSE NULL
            END AS change_usd,
            CASE
                WHEN x.previous_spend_usd > 0
                 AND x.current_spend_usd IS NOT NULL
                    THEN ROUND(
                        100.0 * (
                            x.current_spend_usd / x.previous_spend_usd - 1
                        ),
                        2
                    )
                ELSE NULL
            END AS change_pct
        FROM paired AS x
        LEFT JOIN dim_advertiser AS d
            USING (advertiser_id)
        ORDER BY ABS(change_usd) DESC NULLS LAST, x.advertiser_id
    """,
    "06_targeting_status": """
        SELECT
            required_source,
            data_status,
            targeting_metrics_ready,
            source_checked_on,
            latest_loaded_week,
            reason
        FROM mart_targeting_coverage
        ORDER BY required_source
    """,
}


def save_chart(fig, filename: str) -> None:
    fig.update_layout(
        template="plotly_white",
        font=dict(size=13),
        title_x=0.02,
    )
    # Một bản plotly.min.js dùng chung cho các HTML, xem được khi offline
    fig.write_html(
        CHART_OUT / filename,
        include_plotlyjs="directory",
        full_html=True,
    )


def save_png(fig, filename: str) -> None:
    fig.savefig(CHART_OUT / filename, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def static_charts(trend, concentration, distribution, movers) -> None:
    """Small static PNGs for the repo; interactive HTML remains available."""
    weeks = [
        pd.Timestamp(value).date().isoformat() for value in trend["week_start_date"]
    ]
    spend_m = trend["total_spend_usd"] / 1_000_000

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(weeks, spend_m, marker="o", linewidth=2.5, color="#2259a5")
    for week, value in zip(weeks, spend_m, strict=True):
        ax.annotate(
            f"${value:.1f}m",
            (week, value),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
        )
    ax.set(
        title="Observed USD spend by week", xlabel="Week start", ylabel="USD millions"
    )
    ax.set_ylim(min(spend_m) * 0.9, max(spend_m) * 1.13)
    ax.grid(axis="y", alpha=0.25)
    save_png(fig, "01_trend.png")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(
        weeks,
        concentration["top10_share_pct"],
        marker="o",
        linewidth=2.5,
        color="#14806e",
    )
    ax.set(
        title="Top 10 advertiser share by week", xlabel="Week start", ylabel="Share (%)"
    )
    ax.grid(axis="y", alpha=0.25)
    save_png(fig, "02_top10_share.png")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(
        weeks, concentration["observed_hhi"], marker="o", linewidth=2.5, color="#c87922"
    )
    ax.set(
        title="Observed advertiser concentration (HHI)",
        xlabel="Week start",
        ylabel="HHI, 0-10,000",
    )
    ax.grid(axis="y", alpha=0.25)
    save_png(fig, "03_hhi.png")

    latest_week = distribution["week_start_date"].max()
    latest_week_label = pd.Timestamp(latest_week).date().isoformat()
    latest = distribution[distribution["week_start_date"] == latest_week]
    latest = latest.sort_values("bucket_order")
    fig, ax = plt.subplots(figsize=(9, 4.5))
    bars = ax.bar(latest["spend_bucket"], latest["advertiser_count"], color="#6785b2")
    ax.bar_label(bars, padding=3)
    ax.set(
        title=f"Weekly spend distribution - {latest_week_label}",
        xlabel="USD spend bucket",
        ylabel="Advertisers",
    )
    ax.set_ylim(0, max(latest["advertiser_count"]) * 1.14)
    ax.tick_params(axis="x", rotation=15)
    ax.grid(axis="y", alpha=0.25)
    save_png(fig, "04_distribution.png")

    both = movers[movers["cohort"] == "both_weeks"].copy()
    selected = pd.concat(
        [both.nlargest(5, "change_usd"), both.nsmallest(5, "change_usd")]
    )
    selected = selected.sort_values("change_usd")
    labels = [
        f"{str(name or advertiser_id)[:38]} ({advertiser_id[-5:]})"
        for name, advertiser_id in zip(
            selected["advertiser_name"], selected["advertiser_id"], strict=True
        )
    ]
    fig, ax = plt.subplots(figsize=(11, 6))
    values = selected["change_usd"] / 1_000_000
    colors = ["#bd4c4c" if value < 0 else "#14806e" for value in values]
    ax.barh(labels, values, color=colors)
    ax.axvline(0, color="#444444", linewidth=0.8)
    ax.set(
        title="Largest comparable advertiser changes, latest two weeks",
        xlabel="Change in USD millions",
    )
    ax.grid(axis="x", alpha=0.25)
    save_png(fig, "05_movers.png")


def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit(f"Không thấy {DB_PATH}\nHãy chạy make week6 rồi thử lại")

    for folder in (OUT, SQL_OUT, CHART_OUT):
        folder.mkdir(parents=True, exist_ok=True)

    results: dict[str, pd.DataFrame] = {}

    # Chỉ đọc warehouse, giới hạn RAM để không tranh tài nguyên với Mac
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 2")

    try:
        for name, sql in QUERIES.items():
            (SQL_OUT / f"{name}.sql").write_text(
                sql.strip() + "\n",
                encoding="utf-8",
            )
            df = con.execute(sql).fetchdf()
            df.to_csv(OUT / f"{name}.csv", index=False)
            results[name] = df
            print(f"{name}: {len(df)} rows")

        # Đối soát mart trend với fact trước khi vẽ biểu đồ
        fact_rows, distinct_advertisers, fact_total = con.execute("""
            SELECT COUNT(*), COUNT(DISTINCT advertiser_id), SUM(amount_usd)
            FROM fct_advertiser_weekly_spend
        """).fetchone()

        mart_total = con.execute("""
            SELECT SUM(total_spend_usd)
            FROM mart_spend_trend_weekly
        """).fetchone()[0]

        if fact_total != mart_total:
            raise RuntimeError(f"Fact {fact_total} khác trend mart {mart_total}")
    finally:
        con.close()

    trend = results["01_trend"].copy()
    concentration = results["02_concentration"].copy()
    distribution = results["03_distribution"].copy()
    movers = results["05_movers"].copy()
    targeting = results["06_targeting_status"].copy()

    if len(trend) < 2:
        raise RuntimeError("Cần ít nhất hai tuần để phân tích WoW")

    if (
        trend["week_start_date"].iloc[-1] - trend["week_start_date"].iloc[-2]
    ).days != 7:
        raise RuntimeError("Hai tuần cuối không liền nhau, không phân tích movers")

    for column in ("total_spend_usd", "wow_change_pct"):
        trend[column] = pd.to_numeric(
            trend[column],
            errors="coerce",
        )

    for column in ("top10_share_pct", "observed_hhi"):
        concentration[column] = pd.to_numeric(
            concentration[column],
            errors="coerce",
        )

    movers["change_usd"] = pd.to_numeric(
        movers["change_usd"],
        errors="coerce",
    )

    fig = px.line(
        trend,
        x="week_start_date",
        y="total_spend_usd",
        markers=True,
        hover_data=["advertiser_count", "wow_change_pct"],
        title="Observed USD spend by week",
        labels={
            "week_start_date": "Tuần bắt đầu",
            "total_spend_usd": "USD",
        },
    )
    save_chart(fig, "01_trend.html")

    fig = px.line(
        concentration,
        x="week_start_date",
        y="top10_share_pct",
        markers=True,
        title="Top 10 advertiser share trong từng tuần",
        labels={
            "week_start_date": "Tuần bắt đầu",
            "top10_share_pct": "%",
        },
    )
    save_chart(fig, "02_top10_share.html")

    fig = px.line(
        concentration,
        x="week_start_date",
        y="observed_hhi",
        markers=True,
        title="HHI của advertiser có quan sát",
        labels={
            "week_start_date": "Tuần bắt đầu",
            "observed_hhi": "HHI, thang 0–10.000",
        },
    )
    save_chart(fig, "03_hhi.html")

    latest_week = distribution["week_start_date"].max()
    latest_distribution = distribution[
        distribution["week_start_date"] == latest_week
    ].sort_values("bucket_order")
    if int(latest_distribution["advertiser_count"].sum()) != int(
        trend["advertiser_count"].iloc[-1]
    ):
        raise RuntimeError("Spend buckets không khớp advertiser count tuần cuối")

    fig = px.bar(
        latest_distribution,
        x="spend_bucket",
        y="advertiser_count",
        title=f"Phân phối advertiser theo spend — {latest_week}",
        labels={
            "spend_bucket": "Spend trong tuần",
            "advertiser_count": "Số advertiser",
        },
    )
    save_chart(fig, "04_distribution.html")

    comparable = movers[movers["cohort"] == "both_weeks"].copy()
    top_up = comparable.nlargest(5, "change_usd")
    top_down = comparable.nsmallest(5, "change_usd")
    selected = pd.concat([top_up, top_down])
    selected["direction"] = selected["change_usd"].apply(
        lambda value: "Tăng" if value >= 0 else "Giảm"
    )
    selected["label"] = selected["advertiser_id"]

    fig = px.bar(
        selected,
        x="change_usd",
        y="label",
        color="direction",
        orientation="h",
        title="Advertiser tăng/giảm mạnh — chỉ nhóm có mặt cả hai tuần",
        labels={
            "change_usd": "Thay đổi USD",
            "label": "Advertiser ID",
        },
    )
    save_chart(fig, "05_movers.html")
    static_charts(trend, concentration, distribution, movers)

    summary = {
        "first_week": pd.Timestamp(trend["week_start_date"].min()).date().isoformat(),
        "last_week": pd.Timestamp(trend["week_start_date"].max()).date().isoformat(),
        "weeks_loaded": len(trend),
        "fact_rows": fact_rows,
        "distinct_advertisers": distinct_advertisers,
        "fact_total_usd": str(fact_total),
        "last_week_wow_pct": float(trend["wow_change_pct"].iloc[-1]),
        "last_week_top10_share_pct": float(concentration["top10_share_pct"].iloc[-1]),
        "last_week_hhi": float(concentration["observed_hhi"].iloc[-1]),
        "targeting_ready": bool(targeting["targeting_metrics_ready"].all()),
    }

    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Evidence can be checked without copying raw rows into Git
    evidence = {
        "warehouse_sha256": hashlib.sha256(DB_PATH.read_bytes()).hexdigest(),
        "scope": "observed advertiser-week USD spend in loaded weeks, not US-only",
        "metrics": summary,
        "weekly_trend": json.loads(trend.to_json(orient="records", date_format="iso")),
        "weekly_concentration": json.loads(
            concentration.to_json(orient="records", date_format="iso")
        ),
        "latest_distribution": json.loads(
            latest_distribution.to_json(orient="records", date_format="iso")
        ),
        "mover_cohorts": movers["cohort"].value_counts().to_dict(),
        "top_increase": json.loads(
            top_up[["advertiser_id", "change_usd"]].to_json(orient="records")
        ),
        "top_decrease": json.loads(
            top_down[["advertiser_id", "change_usd"]].to_json(orient="records")
        ),
        "targeting_status": json.loads(
            targeting.to_json(orient="records", date_format="iso")
        ),
        "query_sha256": {
            name: hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()
            for name, sql in QUERIES.items()
        },
    }
    EVIDENCE_OUT.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"SQL kiểm chứng: {SQL_OUT}")
    print(f"Biểu đồ HTML: {CHART_OUT}")
    print(f"CSV aggregate: {OUT}")


if __name__ == "__main__":
    main()
