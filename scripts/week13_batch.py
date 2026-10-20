from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
GOLD_DB = ROOT / "data/tmp/week4/warehouse.duckdb"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", help="Chủ nhật YYYY-MM-DD; mặc định Gold mới nhất")
    parser.add_argument("--include-retrospective", action="store_true")
    args = parser.parse_args()

    with duckdb.connect(str(GOLD_DB), read_only=True) as con:
        latest = con.execute(
            "SELECT MAX(week_start_date) FROM fct_advertiser_weekly_spend"
        ).fetchone()[0]
    cutoff = args.cutoff or latest.isoformat()
    if cutoff != latest.isoformat():
        raise ValueError("Batch live chỉ chạy cutoff Gold mới nhất")

    commands = [
        ["week13_register.py", "--cutoff", cutoff, "--promote"],
        ["week13_score.py", "--cutoff", cutoff, "--execute"],
    ]
    if args.include_retrospective:
        previous = (latest - timedelta(days=7)).isoformat()
        commands.extend(
            [
                ["week13_register.py", "--cutoff", previous],
                [
                    "week13_score.py",
                    "--cutoff",
                    previous,
                    "--retrospective",
                    "--execute",
                ],
            ]
        )
    commands.append(["week13_monitor.py"])
    for command in commands:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / command[0]), *command[1:]],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
