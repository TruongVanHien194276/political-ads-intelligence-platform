import json
from datetime import date

import pytest

from scripts import week7_flow as flow


def test_logical_date_selects_previous_sunday() -> None:
    assert flow.week_for_logical_date("2026-10-12") == date(2026, 10, 11)

    with pytest.raises(ValueError, match="thứ Hai"):
        flow.week_for_logical_date("2026-10-11")


def test_source_correction_gets_new_publish_key() -> None:
    original = flow.selection("2026-10-12", "c002dfbcff2fe0ae")
    corrected = flow.selection("2026-10-12", "f002dfbcff2fe0ae")

    assert original["week_start"] == corrected["week_start"] == "2026-10-11"
    assert original["publish_key"] != corrected["publish_key"]
    assert flow.selection("2026-10-12", "c002dfbcff2fe0ae") == original


def test_latest_snapshot_selects_newest_verified_source(tmp_path, monkeypatch) -> None:
    older = tmp_path / "run_id=1111111111111111"
    newer = tmp_path / "run_id=2222222222222222"
    older.mkdir()
    newer.mkdir()
    manifests = {
        older: {
            "run_id": "1111111111111111",
            "source_modified_utc": "2026-10-13T00:00:00+00:00",
        },
        newer: {
            "run_id": "2222222222222222",
            "source_modified_utc": "2026-10-14T00:00:00+00:00",
        },
    }

    monkeypatch.setattr(flow, "source_runs", lambda week: [newer, older])
    monkeypatch.setattr(
        flow,
        "verify_local",
        lambda run_dir, run_id: manifests[run_dir],
    )

    assert flow.latest_snapshot(date(2026, 10, 11)) == (newer, manifests[newer])


def test_transform_fails_once_then_retries_same_candidate(
    tmp_path, monkeypatch
) -> None:
    info = flow.selection("2026-10-12", "c002dfbcff2fe0ae")
    monkeypatch.setattr(flow, "RUNS", tmp_path)
    monkeypatch.setattr(flow, "already_published", lambda key: False)
    candidate = flow.candidate_path(info)
    candidate.parent.mkdir(parents=True)
    candidate.touch()
    commands = []
    monkeypatch.setattr(
        flow,
        "run_command",
        lambda command, db_path, week: commands.append((command, db_path, week)),
    )

    with pytest.raises(RuntimeError, match="Lỗi mô phỏng"):
        flow.transform(info, fail_once=True)

    assert commands == []
    assert (candidate.parent / "fail-once-transform.marker").is_file()

    flow.transform(info, fail_once=True)
    assert len(commands) == 3
    assert all(db_path == candidate for _, db_path, _ in commands)
    assert all(week == "2026-10-11" for _, _, week in commands)


def test_published_run_is_noop(tmp_path, monkeypatch) -> None:
    info = flow.selection("2026-10-12", "c002dfbcff2fe0ae")
    monkeypatch.setattr(flow, "RUNS", tmp_path)
    monkeypatch.setattr(flow, "already_published", lambda key: True)

    flow.load(info)
    flow.transform(info, fail_once=False)
    flow.quality(info)
    flow.publish(info)

    assert not flow.slot(info).exists()


def test_quality_failure_cannot_replace_live(tmp_path, monkeypatch) -> None:
    info = flow.selection("2026-10-12", "c002dfbcff2fe0ae")
    monkeypatch.setattr(flow, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(flow, "already_published", lambda key: False)
    live = tmp_path / "live.duckdb"
    live.write_bytes(b"published")
    monkeypatch.setattr(flow.week6, "LIVE", live)
    candidate = flow.candidate_path(info)
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"bad candidate")
    (candidate.parent / "quality.json").write_text(
        json.dumps({"candidate_sha256": flow.file_sha256(candidate)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        flow,
        "latest_snapshot",
        lambda week: (tmp_path, {"run_id": info["source_run_id"]}),
    )
    monkeypatch.setattr(
        flow.week6,
        "evaluate",
        lambda path: [
            {"name": "negative_spend_zero", "severity": "BLOCK", "status": "FAIL"}
        ],
    )

    with pytest.raises(RuntimeError, match="negative_spend_zero"):
        flow.publish(info)

    assert live.read_bytes() == b"published"
    assert candidate.read_bytes() == b"bad candidate"
