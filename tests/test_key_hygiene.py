"""D22: the API key is sent only as a request header and shows up nowhere else."""

import json
import logging
import uuid
from decimal import Decimal

import httpx2
import pytest
from pydantic import SecretStr

from docket.evaluation import write_report
from docket.llm.anthropic import AnthropicTransport, OutputMode
from docket.llm.budget import Budget, DailyBudget, RunBudget, load_prices
from docket.llm.errors import MaybeBilled, NoAnswer, UnreadableAnswer
from docket.llm.gateway import Gateway, Mode
from docket.llm.replay import ReplayStore
from docket.prompts import choose_request
from docket.settings import Settings


def reply(text):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 5},
    }


async def test_sentinel_key_never_leaves_the_header(postgres_engine, tmp_path, caplog):
    # Built at run time so no key-like string sits in the repository.
    sentinel = "sentinel-" + uuid.uuid4().hex
    limited = httpx2.Response(
        429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}
    )
    queue = [
        httpx2.Response(200, json=reply('{"record_id":"UNIT-0001"}')),
        httpx2.Response(200, json=reply("not json")),
        *[limited] * 5,
        httpx2.ReadTimeout("slow"),
        httpx2.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "no"}}
        ),
    ]
    headers = []
    request_bodies = []

    async def api(request):
        headers.append(dict(request.headers))
        request_bodies.append(request.content)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def no_sleep(_):
        return None

    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr(sentinel),
        docket_model_provider="anthropic",
        docket_model="claude-haiku-5-5",
    )
    transport = AnthropicTransport(
        settings,
        256,
        httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
        mode=OutputMode(),
    )
    store = ReplayStore(tmp_path / "anthropic.jsonl")
    gateway = Gateway(
        mode=Mode.RECORD,
        model="claude-haiku-5-5",
        store=store,
        budget=Budget(RunBudget(Decimal(1)), DailyBudget(postgres_engine, Decimal(1))),
        transport=transport,
        prices=load_prices().ceiling("claude-haiku-5-5"),
        sleep=no_sleep,
        provider="anthropic",
    )
    errors = []
    with caplog.at_level(logging.DEBUG):
        for text in ["one", "two", "three", "four", "five"]:
            call = choose_request(text, ["UNIT-0001"])
            try:
                await gateway.call(**call, source="synthetic_corpus")
            except (UnreadableAnswer, NoAnswer, MaybeBilled, Exception) as exc:
                errors.append(exc)
    for sent in headers:
        assert sent["authorization"] == "Bearer " + sentinel
        assert all(sentinel not in value for name, value in sent.items() if name != "authorization")
        assert sentinel not in str(request_bodies)
    assert len(errors) == 4
    assert caplog.records, "debug logging must be captured for this check to mean anything"
    report = {
        "date": "test",
        "seed": 11,
        "status": "PENDING",
        "measurement": "anthropic",
        "model": "claude-haiku-5-5",
        "provenance": [
            {"provider": "anthropic", "model": "claude-haiku-5-5", "recorded": "2026-11-12"}
        ],
        "baseline": None,
    }
    written = write_report(report, tmp_path / "reports")
    places = {
        "logs": "\n".join(
            record.getMessage() + repr(record.args) + repr(record.__dict__)
            for record in caplog.records
        ),
        "errors": "\n".join(
            f"{exc!s} {exc!r} {exc.__cause__!s} {exc.__cause__!r}" for exc in errors
        ),
        "recordings": (tmp_path / "anthropic.jsonl").read_text(),
        "report": written.read_text() + written.with_suffix(".md").read_text(),
        "reprs": repr(settings) + repr(transport) + repr(gateway.__dict__) + str(settings),
        "dump": json.dumps(settings.model_dump(mode="json")),
    }
    for place, text in places.items():
        assert sentinel not in text, place


def test_missing_key_message_names_the_setting_only():
    with pytest.raises(ValueError) as raised:
        AnthropicTransport(Settings(_env_file=None, anthropic_api_key=SecretStr("")), 256)
    assert str(raised.value) == "ANTHROPIC_API_KEY is required for live calls"
