"""A replay never overwrites the report of a paid record run."""

import json

import pytest

from docket.evaluation import write_report


def report(mode):
    return {
        "date": "2026-11-13",
        "seed": 11,
        "mode": mode,
        "status": "PENDING-SIMULATION",
        "baseline": None,
    }


def test_a_replay_cannot_overwrite_a_record_run_report(tmp_path):
    path = write_report(report("record"), tmp_path)
    saved = path.read_bytes()
    with pytest.raises(ValueError, match="regenerate_live_report"):
        write_report(report("replay"), tmp_path)
    assert path.read_bytes() == saved


def test_a_report_that_keeps_its_record_runs_may_replace_one(tmp_path):
    write_report(report("record"), tmp_path)
    path = write_report({**report("record"), "record_runs": [{"mode": "record"}]}, tmp_path)
    assert json.loads(path.read_text())["record_runs"] == [{"mode": "record"}]


def test_replays_may_replace_replays(tmp_path):
    write_report(report("replay"), tmp_path)
    write_report(report("replay"), tmp_path)
