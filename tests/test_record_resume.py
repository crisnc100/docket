"""A second record run over the same seed sends only the requests that have no answer."""

import json
import shutil
from decimal import Decimal

from pydantic import SecretStr

from docket.evaluation import evaluate
from docket.llm.anthropic import Reply
from docket.settings import Settings
from tests.test_graded_evidence import corpus_labels


class FakeTransport:
    calls = 0

    def __init__(self, settings, max_output_tokens):
        pass

    async def __call__(self, prompt, schema, *, system_prompt=""):
        FakeTransport.calls += 1
        properties = schema.model_json_schema()
        if "category" in properties["properties"]:
            answer = {"category": "misc"}
        else:
            answer = {"record_id": "none"}
        return Reply(json.dumps(answer), "end_turn", 100, 5, "json_schema", 300)


async def test_second_record_run_sends_only_missing_requests(tmp_path, monkeypatch):
    folder, labels = corpus_labels(11)
    chosen = labels[:6]
    target = tmp_path / folder
    target.mkdir(parents=True)
    for label in chosen:
        (target / label.filename).write_bytes((folder / label.filename).read_bytes())
    (target / "labels.jsonl").write_text(
        "".join(label.model_dump_json() + "\n" for label in chosen)
    )
    shutil.copytree("config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("docket.runtime.AnthropicTransport", FakeTransport)
    FakeTransport.calls = 0
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("test-key"),
        docket_replay_path=str(tmp_path / "anthropic.jsonl"),
        docket_daily_budget_usd=5,
    )
    first = await evaluate(11, "record", Decimal("1"), settings=settings, date="test")
    sent = FakeTransport.calls
    assert sent > 0 and first["requests_to_send"] == sent
    assert first["recorded_before_run"] == 0
    archive = (tmp_path / "anthropic.jsonl").read_bytes()
    assert len(archive.splitlines()) == sent

    second = await evaluate(11, "record", Decimal("1"), settings=settings, date="test")
    assert FakeTransport.calls == sent
    assert second["recorded_before_run"] == sent and second["requests_to_send"] == 0
    assert (tmp_path / "anthropic.jsonl").read_bytes() == archive
    assert second["docket_results"] == first["docket_results"]
    assert second["live_run_cost_usd"] == "0"
