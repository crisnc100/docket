"""D23: a live report carries the live banner and never mixes live with simulated answers."""

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

from docket.core.scoring import load_scoring_config
from docket.evaluation import baseline, evaluate, measurement_label, write_report
from docket.llm.replay import Recording
from docket.settings import Settings
from tests.test_graded_evidence import corpus_labels


def live(name: str, recorded: str = "2026-11-13") -> Recording:
    return Recording(
        key=hashlib.sha256(name.encode()).hexdigest(),
        outcome="answer",
        reason=None,
        output={"record_id": "none"},
        raw_text='{"record_id":"none"}',
        cost_usd="0.000105",
        input_tokens=1_000,
        output_tokens=10,
        latency_ms=400,
        output_mode="json_schema",
        source="synthetic_corpus",
        provenance={"provider": "anthropic", "model": "claude-haiku-5-5", "recorded": recorded},
    )


def simulated(name: str) -> Recording:
    return Recording(
        key=hashlib.sha256(name.encode()).hexdigest(),
        output={"record_id": "none"},
        cost_usd=None,
        source="synthetic_corpus",
        provenance={"simulated_by": "claude-haiku-5-5", "standing_in_for": "openai/gpt-4o-mini"},
    )


def openrouter(name: str) -> Recording:
    return Recording(
        key=hashlib.sha256(name.encode()).hexdigest(),
        output={"record_id": "none"},
        cost_usd=".01",
        source="synthetic_corpus",
    )


def test_live_answers_are_their_own_measurement():
    assert measurement_label([live("a"), live("b")]) == "anthropic"


@pytest.mark.parametrize("other", [simulated, openrouter])
def test_live_answers_never_mix(other):
    with pytest.raises(ValueError, match="cannot mix live"):
        measurement_label([live("a"), other("b")])
    with pytest.raises(ValueError, match="cannot mix live"):
        measurement_label([other("b"), live("a")])


def test_live_entry_without_its_measurements_is_refused():
    with pytest.raises(ValueError, match="live answer needs"):
        Recording(
            key=hashlib.sha256(b"x").hexdigest(),
            output={"record_id": "none"},
            cost_usd="0.0001",
            source="synthetic_corpus",
            provenance={"provider": "anthropic", "model": "claude-haiku-5-5", "recorded": "d"},
        )


def test_unreadable_entry_needs_a_known_reason_and_no_output():
    fields = live("x").model_dump()
    with pytest.raises(ValueError):
        Recording(**{**fields, "outcome": "unreadable", "reason": "other", "output": None})
    with pytest.raises(ValueError):
        Recording(**{**fields, "outcome": "unreadable", "reason": "cut"})
    entry = Recording(**{**fields, "outcome": "unreadable", "reason": "cut", "output": None})
    assert entry.reason == "cut"


async def run(entries, tmp_path, monkeypatch):
    folder, labels = corpus_labels(7)
    original = labels[0]
    shutil.copytree("config", tmp_path / "config")
    target = tmp_path / folder
    target.mkdir(parents=True)
    (target / original.filename).write_bytes((folder / original.filename).read_bytes())
    (target / "labels.jsonl").write_text(original.model_dump_json() + "\n")
    result = baseline(original, folder, load_scoring_config())
    monkeypatch.chdir(tmp_path)
    Path("anthropic.jsonl").touch()
    monkeypatch.setattr("docket.evaluation.pending_requests", lambda *args: {})

    async def supplied_outcomes(*args):
        return [result], entries, Decimal(0)

    monkeypatch.setattr("docket.evaluation.model_outcomes", supplied_outcomes)
    return await evaluate(
        7,
        "replay",
        Decimal(0),
        settings=Settings(_env_file=None, docket_replay_path="anthropic.jsonl"),
        date="test",
    )


async def test_live_report_carries_the_banner_and_cost(tmp_path, monkeypatch):
    report = await run([live("a"), live("b")], tmp_path, monkeypatch)
    assert report["measurement"] == "anthropic"
    assert report["provenance"] == [
        {"provider": "anthropic", "model": "claude-haiku-5-5", "recorded": "2026-11-13"}
    ]
    assert "simulation" not in report
    assert report["model_cost_usd"] == "0.000210"
    path = write_report(report)
    banner = "Model answers measured live from claude-haiku-5-5 on the Anthropic API on 2026-11-13."
    assert path.with_suffix(".md").read_text().startswith(banner + "\n\n")
    assert json.loads(path.read_text())["banner"] == banner
    assert "simulated" not in path.with_suffix(".md").read_text().lower()


async def test_live_banner_names_both_dates_when_a_pass_spans_two(tmp_path, monkeypatch):
    report = await run([live("a", "2026-11-13"), live("b", "2026-11-14")], tmp_path, monkeypatch)
    path = write_report(report)
    assert (
        path.with_suffix(".md")
        .read_text()
        .startswith(
            "Model answers measured live from claude-haiku-5-5 on the Anthropic API "
            "on 2026-11-13 to 2026-11-14."
        )
    )


async def test_eval_refuses_a_mixed_report(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="cannot mix live"):
        await run([live("a"), simulated("b")], tmp_path, monkeypatch)


async def test_live_eval_needs_the_anthropic_key_and_a_budget_under_the_cap(tmp_path, monkeypatch):
    shutil.copytree("config", tmp_path / "config")
    (tmp_path / "corpus/seed-11").mkdir(parents=True)
    shutil.copy("corpus/seed-11/labels.jsonl", tmp_path / "corpus/seed-11/labels.jsonl")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("docket.evaluation.pending_requests", lambda *args: {})
    keyless = Settings(_env_file=None, docket_replay_path="anthropic.jsonl")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        await evaluate(11, "record", Decimal("2"), settings=keyless, date="test")
    keyed = keyless.model_copy(update={"anthropic_api_key": SecretStr("test-key")})
    with pytest.raises(ValueError, match="DOCKET_RUN_BUDGET_CAP_USD"):
        await evaluate(11, "record", Decimal("5.01"), settings=keyed, date="test")
