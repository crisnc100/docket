"""Default settings replay answers that exist in the repository."""

import json
from decimal import Decimal
from pathlib import Path

from docket.evaluation import evaluate
from docket.llm.replay import ReplayStore
from docket.settings import Settings


def test_default_replay_path_is_an_existing_archive():
    settings = Settings(_env_file=None)
    assert settings.docket_model_provider == "anthropic"
    assert settings.docket_model == "claude-haiku-5-5"
    path = Path(settings.docket_replay_path)
    assert path == Path("evals/recordings/anthropic.jsonl")
    assert path.is_file()
    entries = ReplayStore(path).entries()
    assert entries
    # Every default answer is a live one from the default model, never a simulated one.
    assert all(entry.live for entry in entries)
    assert {entry.provenance["model"] for entry in entries if entry.provenance} == {
        "claude-haiku-5-5"
    }


async def test_default_replay_eval_finds_every_answer(tmp_path, monkeypatch):
    async def no_workflows(*args):
        raise AssertionError("stop after the answer lookup")

    monkeypatch.setattr("docket.evaluation.model_outcomes", no_workflows)
    manifest = Path("evals/manifests/seed-11.json")
    saved = manifest.read_bytes()
    try:
        report = None
        try:
            report = await evaluate(11, "replay", Decimal(0), settings=Settings(_env_file=None))
        except AssertionError as exc:
            assert "stop after the answer lookup" in str(exc)
        assert report is None, "a default replay must reach the workflows, not stop as pending"
    finally:
        manifest.write_bytes(saved)
    assert json.loads(saved)["model"] == Settings(_env_file=None).docket_model
