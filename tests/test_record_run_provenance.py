"""The published live reports keep the evidence of the paid record runs behind them."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from docket.evaluation import with_record_runs


def stored(seed):
    return json.loads(Path(f"evals/reports/2026-11-13-seed-{seed}.json").read_text())


@pytest.mark.parametrize(("seed", "runs"), [(7, 2), (11, 1)])
def test_live_reports_say_they_came_from_record_runs_and_match_the_cost_summary(seed, runs):
    report = stored(seed)
    [summary_path] = Path("evals/cost").glob("*-claude-haiku-5-5.json")
    summary = json.loads(summary_path.read_text())
    assert report["mode"] == "record"
    assert len(report["record_runs"]) == runs
    assert all(run["mode"] == "record" for run in report["record_runs"])
    assert Decimal(report["live_run_cost_usd"]) == Decimal(summary["seeds"][str(seed)]["spend_usd"])
    assert Decimal(report["live_run_cost_usd"]) == sum(
        Decimal(run["live_run_cost_usd"]) for run in report["record_runs"]
    )
    assert report["budget_usd"] == report["record_runs"][-1]["budget_usd"]
    assert (
        sum(run["requests_to_send"] for run in report["record_runs"])
        >= report["live"]["requests_answered"]
    )
    assert f"{runs} live record run" in Path(f"evals/reports/2026-11-13-seed-{seed}.md").read_text()


def replayed():
    return {
        "seed": 7,
        "model": "claude-haiku-5-5",
        "mode": "replay",
        "budget_usd": "0",
        "live_run_cost_usd": "0",
        "status": "FAIL",
    }


def run(**changes):
    return {
        "seed": 7,
        "model": "claude-haiku-5-5",
        "mode": "record",
        "budget_usd": "2.00",
        "live_run_cost_usd": "0.01",
        "recorded_before_run": 0,
        "requests_to_send": 220,
        **changes,
    }


def test_record_runs_carry_forward_onto_a_replayed_report():
    report = with_record_runs(
        replayed(), [run(), run(budget_usd="1.98", live_run_cost_usd="0.002")], ["a", "b"]
    )
    assert report["mode"] == "record"
    assert report["budget_usd"] == "1.98"
    assert report["live_run_cost_usd"] == "0.012"
    assert [entry["source"] for entry in report["record_runs"]] == ["a", "b"]
    assert report["record_runs"][1]["requests_to_send"] == 220


@pytest.mark.parametrize("bad", [run(mode="replay"), run(seed=11), run(model="other")])
def test_only_record_runs_of_the_same_seed_and_model_are_carried(bad):
    with pytest.raises(ValueError):
        with_record_runs(replayed(), [bad], ["a"])
