import asyncio
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docket.cli import app
from docket.config import Category
from docket.core.scoring import load_scoring_config
from docket.corpus import Label
from docket.evaluation import (
    Outcome,
    evaluate,
    metrics,
    shadow_diff,
    summarize,
    wilson,
    write_report,
)
from docket.settings import Settings


def label(name="clean", expected="filed", record_id="UNIT-0001"):
    return Label(
        document_id="doc-1",
        filename="doc.pdf",
        case_type=name,
        expected=expected,
        record_id=record_id,
        category=Category.LEASE,
        subject="synthetic evaluation test",
    )


def outcome(status="would_file", record_id="UNIT-0001", score=".6", tier="strong"):
    return Outcome("doc-1", status, "test-reason", record_id, "lease", score, tier)


def test_metrics_curve_intervals_and_two_signal_tier_gate():
    labels = [label(), label("id_in_filename_only", "review"), label("clean")]
    results = [outcome(), outcome(score=".9", tier="medium"), outcome(record_id="UNIT-0002")]
    values = metrics(labels, results, Decimal(".55"))
    assert values["auto_file_count"] == 2 and values["correct_file_count"] == 1
    assert values["wrong_file_count"] == 1 and values["auto_file_precision"] == 0.5
    assert values["auto_file_recall"] == 0.5 and values["review_rate"] == 1 / 3
    low, high = values["precision_wilson_95"]
    assert low < 0.5 < high
    assert wilson(0, 0) is None
    assert wilson(0, 10)[0] == 0 and wilson(10, 10)[1] == 1
    report = summarize(labels, results, load_scoring_config())
    assert len(report["curve"]) == 8
    assert report["curve"][-1]["auto_file_count"] == 0
    assert report["per_case_type"]["id_in_filename_only"]["auto_file_count"] == 0


@pytest.mark.parametrize(
    "new,old,classification",
    [
        (outcome(), outcome(record_id="UNIT-0002"), "docket_only_correct"),
        (outcome(record_id="UNIT-0002"), outcome(), "baseline_only_correct"),
        (outcome(record_id="UNIT-0002"), outcome(record_id="UNIT-0003"), "both_wrong"),
    ],
)
def test_shadow_classifies_each_disagreement_against_gold(new, old, classification):
    result = shadow_diff([label()], [new], [old])
    assert len(result["disagreements"]) == 1
    assert result["disagreements"][0]["classification"] == classification
    assert result["disagreements"][0]["gold"]["record_id"] == "UNIT-0001"
    assert shadow_diff([label()], [new], [new])["disagreements"] == []


def test_simulated_banner_is_first_and_cost_is_not_invented(tmp_path):
    baseline = summarize([label()], [outcome()], load_scoring_config())
    report = dict(
        date="test",
        seed=11,
        status="PASS",
        docket=baseline,
        baseline=baseline,
        floors={},
        measurement="simulated",
        provenance=[{"simulated_by": "operator-test-fixture", "standing_in_for": "target"}],
        model_cost_usd=None,
    )
    path = write_report(report, tmp_path)
    text = path.with_suffix(".md").read_text()
    assert text.startswith(
        "Model responses simulated by operator-test-fixture, standing in for target; "
        "not an OpenRouter measurement."
    )
    assert json.loads(path.read_text())["model_cost_usd"] is None


async def test_export_and_pending_replay_reproduce_without_answers_or_network(
    tmp_path, monkeypatch
):
    shutil.copytree("config", tmp_path / "config")
    shutil.copytree("corpus/seed-11", tmp_path / "corpus/seed-11")
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)
    exported = await evaluate(11, "export", Decimal(0), settings=settings, date="test")
    replayed = await evaluate(11, "replay", Decimal(0), settings=settings, date="test")
    assert exported["status"] == replayed["status"] == "PENDING-SIMULATION"
    assert exported["baseline"] == replayed["baseline"]
    assert exported["pending_requests"] > 0
    assert exported["pending_requests"] == replayed["pending_requests"]
    assert exported["docket"] is exported["floors"] is None
    assert not Path("evals/answers").exists() and not Path(settings.docket_replay_path).exists()
    manifest = json.loads(Path(exported["manifest"]).read_text())
    assert manifest["request_count"] == len(manifest["keys"]) == exported["pending_requests"]
    result = await asyncio.to_thread(
        CliRunner().invoke, app, ["eval", "--seed", "11", "--mode", "export", "--budget", "0"]
    )
    assert result.exit_code == 0 and "PENDING-SIMULATION" in result.output


async def test_invalid_eval_parameters_are_rejected():
    for mode, budget in [("unknown", "0"), ("replay", "NaN"), ("export", "-1")]:
        with pytest.raises(ValueError):
            await evaluate(11, mode, Decimal(budget))


def test_failed_outcome_is_never_classified_as_correct_review():
    result = shadow_diff(
        [label("tenant_only", "review")],
        [outcome(status="failed")],
        [outcome(status="would_review")],
    )
    assert result["disagreements"][0]["classification"] == "baseline_only_correct"


def test_shadow_cli_ignores_its_own_output_and_keeps_simulation_banner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = Path("evals/reports")
    folder.mkdir(parents=True)
    path = folder / "2026-11-09-seed-11.json"
    data = {
        "banner": (
            "Model responses simulated by operator-test-fixture; not an OpenRouter measurement."
        ),
        "provenance": [{"simulated_by": "operator-test-fixture", "standing_in_for": "target"}],
        "shadow": {"status": "COMPLETE", "disagreements": []},
    }
    path.write_text(json.dumps(data))
    for _ in range(2):
        result = CliRunner().invoke(app, ["shadow-diff"])
        assert result.exit_code == 0, result.output
    output = json.loads(path.with_name(path.stem + "-shadow.json").read_text())
    assert output["banner"] == data["banner"] and output["measurement"] == "simulated"
    assert output["disagreements"] == [] and output["provenance"] == data["provenance"]


def test_shadow_cli_reports_missing_evaluation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["shadow-diff"])
    assert result.exit_code != 0 and "run docket eval first" in result.output
