from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from week12_common import OUT

sns.set_theme(style="whitegrid")
LABELS = {
    "naive_last_week": "Naive",
    "lightgbm_point_quantile": "LightGBM",
    "tft_small": "TFT small",
    "chronos_2_small_zero_shot": "Chronos-2-small",
}
FIG = OUT / "figures"
FIG.mkdir(parents=True, exist_ok=True)

frame = pd.read_parquet(OUT / "all_predictions.parquet")
frame["week_start_date"] = pd.to_datetime(frame["week_start_date"])

# 1. Actual vs forecast: aggregate theo tuần, tránh vẽ chồng 578 điểm
weekly = frame.groupby(["model", "week_start_date"], as_index=False).agg(
    actual=("actual", "sum"), forecast=("point", "sum")
)
plt.figure(figsize=(11, 5))
actual = weekly[weekly["model"] == "naive_last_week"]
plt.plot(actual["week_start_date"], actual["actual"], "k-o", label="Actual")
for model, part in weekly.groupby("model"):
    plt.plot(part["week_start_date"], part["forecast"], marker=".", label=LABELS[model])
plt.title("Actual vs forecast — các test rows đủ điều kiện theo tuần")
plt.ylabel("USD")
plt.legend()
plt.tight_layout()
plt.savefig(FIG / "actual_vs_forecast.png", dpi=160)
plt.close()

# 2. Residual: nếu model lệch có hệ thống sẽ nhìn thấy
frame["residual"] = frame["actual"] - frame["point"]
frame["model_label"] = frame["model"].map(LABELS)
plt.figure(figsize=(10, 5))
sns.boxplot(data=frame, x="model_label", y="residual")
plt.axhline(0, color="black", linewidth=1)
plt.yscale("symlog", linthresh=20_000)
plt.title("Residual theo model — actual trừ forecast")
plt.ylabel("USD")
plt.xlabel("")
plt.tight_layout()
plt.savefig(FIG / "residual.png", dpi=160)
plt.close()

# 3. Interval: chọn một advertiser có nhiều test rows nhất, giữ cùng series
advertiser = (
    frame[frame["model"] == "naive_last_week"]
    .groupby("advertiser_id")
    .size()
    .sort_values(ascending=False)
    .index[0]
)
fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
for ax, (model, part) in zip(
    axes.flat,
    frame[frame["advertiser_id"] == advertiser].groupby("model"),
):
    part = part.sort_values("week_start_date")
    ax.plot(part["week_start_date"], part["actual"], "ko-", label="Actual")
    ax.plot(part["week_start_date"], part["point"], "b.-", label="Point")
    ax.fill_between(
        part["week_start_date"],
        part["p10"],
        part["p90"],
        alpha=0.25,
        label="p10–p90",
    )
    ax.set_title(LABELS[model])
    ax.tick_params(axis="x", rotation=30)
    if model == "chronos_2_small_zero_shot":
        ax.legend(loc="upper left")
fig.suptitle(f"Forecast interval — advertiser {advertiser}")
fig.tight_layout()
fig.savefig(FIG / "interval.png", dpi=160)
plt.close(fig)

print(FIG)
