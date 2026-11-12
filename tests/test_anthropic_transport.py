"""The Anthropic transport against a fake HTTP layer. No test here reaches the network."""

import itertools
import json
import logging
from decimal import Decimal
from typing import Literal

import httpx2
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import select

from docket.activities_schema import Classification, choice_schema
from docket.db import BudgetLedger
from docket.llm.anthropic import JSON_PROMPT, JSON_SCHEMA, AnthropicTransport, OutputMode
from docket.llm.budget import Budget, DailyBudget, RunBudget, load_prices
from docket.llm.errors import MaybeBilled, NoAnswer, ParseError, UnreadableAnswer, UsageError
from docket.llm.exchange import Exchange
from docket.llm.gateway import Gateway, Mode
from docket.llm.replay import ReplayStore, canonical
from docket.prompts import choose_request, classify_request
from docket.settings import Settings

HAIKU = load_prices().ceiling("claude-haiku-5-5")


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: Literal["UNIT-0001", "none"]


def message(text='{"record_id":"UNIT-0001"}', stop="end_turn", input_tokens=120, output_tokens=9):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


class FakeApi:
    """Answers each request with the next queued item: a JSON body, a status, or an error."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            status, body = item
            return httpx2.Response(status, json=body)
        return httpx2.Response(200, json=item)

    def bodies(self):
        return [json.loads(request.content) for request in self.requests]


def settings(key="test-key"):
    return Settings(
        _env_file=None,
        anthropic_api_key=SecretStr(key),
        docket_model_provider="anthropic",
        docket_model="claude-haiku-5-5",
    )


def budget(engine, run="1", daily="1"):
    return Budget(RunBudget(Decimal(run)), DailyBudget(engine, Decimal(daily)))


def spent(engine):
    with engine.connect() as connection:
        return connection.execute(select(BudgetLedger.spent_usd)).scalar_one()


def gateway(api, engine, tmp_path, *, mode=Mode.RECORD, key="test-key", output_mode=None, **kw):
    delays = kw.pop("delays", [])

    async def sleep(seconds):
        delays.append(seconds)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(api))
    transport = AnthropicTransport(
        settings(key),
        256,
        client,
        mode=output_mode or OutputMode(),
        clock=itertools.count(0, 0.25).__next__,
    )
    return Gateway(
        mode=mode,
        model="claude-haiku-5-5",
        store=kw.pop("store", ReplayStore(tmp_path / "anthropic.jsonl")),
        budget=kw.pop("run_budget", budget(engine)),
        transport=transport,
        prices=kw.pop("prices", HAIKU),
        max_output_tokens=256,
        sleep=sleep,
        provider="anthropic",
        today=lambda: "2026-11-12",
        exchange=Exchange(tmp_path / "pending", tmp_path / "answers"),
    )


def choose(text="synthetic lease for unit 1"):
    call = choose_request(text, ["UNIT-0001", "UNIT-0002"])
    call["source"] = "synthetic_corpus"
    return call


# D18: request shape.


async def test_request_shape(postgres_engine, tmp_path):
    api = FakeApi(message('{"category":"lease"}'))
    call = classify_request("synthetic lease")
    result = await gateway(api, postgres_engine, tmp_path).call(**call, source="synthetic_corpus")
    assert result.category.value == "lease"
    [body] = api.bodies()
    assert body["model"] == "claude-haiku-5-5"
    assert body["system"] == call["system_prompt"]
    assert body["messages"] == [{"role": "user", "content": call["prompt"]}]
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"]["effort"] == "low"
    assert body["max_tokens"] == 256
    for name in ("temperature", "top_p", "top_k"):
        assert name not in body
    schema = body["output_config"]["format"]
    assert schema["type"] == "json_schema"
    text = json.dumps(schema["schema"])
    assert "$ref" not in text and "$defs" not in text
    assert schema["schema"]["additionalProperties"] is False
    assert schema["schema"]["properties"]["category"]["enum"] == [
        "lease",
        "inspection",
        "invoice",
        "notice",
        "misc",
    ]


async def test_choice_schema_inlines_the_shortlist_enum(postgres_engine, tmp_path):
    api = FakeApi(message('{"record_id":"UNIT-0002"}'))
    result = await gateway(api, postgres_engine, tmp_path).call(**choose())
    assert result.model_dump(mode="json") == {"record_id": "UNIT-0002"}
    schema = api.bodies()[0]["output_config"]["format"]["schema"]
    assert schema["properties"]["record_id"]["enum"] == ["UNIT-0001", "UNIT-0002", "none"]
    assert schema["additionalProperties"] is False
    assert "$defs" not in schema


# D19: unreadable answers.


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (message("", stop="refusal"), "refusal"),
        (message('{"record_id":"UNIT-00', stop="max_tokens"), "cut"),
        (message("I pick UNIT-0001."), "not_json"),
        (message("[1, 2]"), "not_json"),
        (message('{"record_id":"UNIT-0999"}'), "schema"),
    ],
)
async def test_unreadable_is_charged_recorded_and_replays_as_the_same_error(
    postgres_engine, tmp_path, response, reason
):
    store = ReplayStore(tmp_path / "anthropic.jsonl")
    live = gateway(FakeApi(response), postgres_engine, tmp_path, store=store)
    with pytest.raises(UnreadableAnswer) as raised:
        await live.call(**choose())
    assert raised.value.reason == reason
    assert isinstance(raised.value, ParseError) and raised.value.type == "ParseError"
    cost = HAIKU.cost(120, 9)
    assert live.actual_cost_usd == cost == spent(postgres_engine)
    [entry] = store.entries()
    assert entry.outcome == "unreadable" and entry.reason == reason
    assert entry.output is None
    assert entry.raw_text == response["content"][0]["text"]
    assert entry.cost_usd == str(cost)
    assert (entry.input_tokens, entry.output_tokens) == (120, 9)
    replay = Gateway(mode=Mode.REPLAY, model="claude-haiku-5-5", store=store)
    with pytest.raises(UnreadableAnswer) as replayed:
        await replay.call(**choose())
    assert replayed.value.reason == reason
    assert replay.call_count == 0


async def test_answer_records_the_live_format(postgres_engine, tmp_path):
    store = ReplayStore(tmp_path / "anthropic.jsonl")
    await gateway(FakeApi(message()), postgres_engine, tmp_path, store=store).call(**choose())
    line = json.loads((tmp_path / "anthropic.jsonl").read_text())
    assert line == {
        "key": line["key"],
        "outcome": "answer",
        "reason": None,
        "output": {"record_id": "UNIT-0001"},
        "raw_text": '{"record_id":"UNIT-0001"}',
        "cost_usd": str(HAIKU.cost(120, 9)),
        "input_tokens": 120,
        "output_tokens": 9,
        "latency_ms": 250,
        "output_mode": "json_schema",
        "source": "synthetic_corpus",
        "provenance": {
            "provider": "anthropic",
            "model": "claude-haiku-5-5",
            "recorded": "2026-11-12",
        },
    }
    replay = Gateway(mode=Mode.REPLAY, model="claude-haiku-5-5", store=store)
    assert (await replay.call(**choose())).model_dump(mode="json") == {"record_id": "UNIT-0001"}


async def test_record_mode_refuses_non_synthetic_before_any_request(postgres_engine, tmp_path):
    api = FakeApi(message())
    live = gateway(api, postgres_engine, tmp_path)
    call = choose()
    call["source"] = "http"
    with pytest.raises(Exception, match="synthetic corpus"):
        await live.call(**call)
    assert api.requests == [] and live.call_count == 0


# D20: retries, timeouts and the schema fallback.


@pytest.mark.parametrize(
    "failure",
    [
        (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}),
        (529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}),
        (500, {"type": "error", "error": {"type": "api_error", "message": "oops"}}),
        (503, {"type": "error", "error": {"type": "api_error", "message": "oops"}}),
        httpx2.ConnectError("refused"),
    ],
)
async def test_unbilled_failures_retry_with_backoff_then_succeed(
    postgres_engine, tmp_path, failure
):
    delays = []
    api = FakeApi(failure, failure, message())
    live = gateway(api, postgres_engine, tmp_path, delays=delays)
    assert (await live.call(**choose())).model_dump(mode="json") == {"record_id": "UNIT-0001"}
    assert delays == [1, 2]
    assert len(api.requests) == live.call_count == 3
    assert spent(postgres_engine) == HAIKU.cost(120, 9)


async def test_unbilled_failures_give_up_after_four_retries_as_no_answer(postgres_engine, tmp_path):
    delays = []
    limited = (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
    api = FakeApi(*[limited] * 5)
    store = ReplayStore(tmp_path / "anthropic.jsonl")
    run_budget = budget(postgres_engine)
    live = gateway(
        api, postgres_engine, tmp_path, delays=delays, store=store, run_budget=run_budget
    )
    with pytest.raises(NoAnswer):
        await live.call(**choose())
    assert delays == [1, 2, 4, 8]
    assert len(api.requests) == 5
    assert spent(postgres_engine) == run_budget.run.spent == 0
    assert not (tmp_path / "anthropic.jsonl").exists()


@pytest.mark.parametrize(
    "failure", [httpx2.ReadTimeout("slow"), httpx2.RemoteProtocolError("dropped")]
)
async def test_failure_after_sending_is_charged_worst_case_and_not_retried(
    postgres_engine, tmp_path, failure
):
    delays = []
    api = FakeApi(failure, message())
    run_budget = budget(postgres_engine)
    live = gateway(api, postgres_engine, tmp_path, delays=delays, run_budget=run_budget)
    call = choose()
    with pytest.raises(MaybeBilled):
        await live.call(**call)
    assert len(api.requests) == 1 and delays == []
    worst = run_budget.run.spent
    assert worst > 0 and spent(postgres_engine) == worst == live.actual_cost_usd


async def test_schema_refusal_switches_to_json_in_prompt_once_and_records_the_mode(
    postgres_engine, tmp_path, caplog
):
    refused = (
        400,
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "output_config.format: the compiled grammar is too large",
            },
        },
    )
    mode = OutputMode()
    store = ReplayStore(tmp_path / "anthropic.jsonl")
    api = FakeApi(refused, message(), message('{"category":"lease"}'))
    live = gateway(api, postgres_engine, tmp_path, store=store, output_mode=mode)
    with caplog.at_level(logging.WARNING, logger="docket.llm.anthropic"):
        assert (await live.call(**choose())).model_dump(mode="json") == {"record_id": "UNIT-0001"}
        await live.call(**classify_request("synthetic lease"), source="synthetic_corpus")
    assert mode.current == JSON_PROMPT
    assert sum("Structured output refused" in r.getMessage() for r in caplog.records) == 1
    first, second, third = api.bodies()
    assert "format" in first["output_config"]
    assert "format" not in second["output_config"] and "format" not in third["output_config"]
    assert '"UNIT-0002"' in second["system"] and second["system"].startswith(
        choose()["system_prompt"]
    )
    assert {entry.output_mode for entry in store.entries()} == {JSON_PROMPT}
    assert spent(postgres_engine) == HAIKU.cost(120, 9) * 2


async def test_other_bad_requests_do_not_switch_mode(postgres_engine, tmp_path):
    bad = (
        400,
        {"type": "error", "error": {"type": "invalid_request_error", "message": "bad model"}},
    )
    mode = OutputMode()
    api = FakeApi(bad)
    live = gateway(api, postgres_engine, tmp_path, output_mode=mode)
    with pytest.raises(Exception, match="bad model"):
        await live.call(**choose())
    assert mode.current == JSON_SCHEMA and len(api.requests) == 1


# D21: cost and reservation.


async def test_cost_is_tokens_times_the_snapshot(postgres_engine, tmp_path):
    api = FakeApi(message(input_tokens=1_234, output_tokens=7))
    live = gateway(api, postgres_engine, tmp_path)
    await live.call(**choose())
    expected = Decimal(1_234) * Decimal("0.10") / 10**6 + Decimal(7) * Decimal("0.50") / 10**6
    assert live.actual_cost_usd == expected == spent(postgres_engine)
    assert Decimal(ReplayStore(tmp_path / "anthropic.jsonl").entries()[0].cost_usd) == expected


async def test_reservation_uses_the_snapshot_worst_case(postgres_engine, tmp_path):
    reserved = []

    class Watch(RunBudget):
        def reserve(self, amount):
            reserved.append(amount)
            super().reserve(amount)

    run_budget = Budget(Watch(Decimal(1)), DailyBudget(postgres_engine, Decimal(1)))
    await gateway(FakeApi(message()), postgres_engine, tmp_path, run_budget=run_budget).call(
        **choose()
    )
    call = choose()
    request = {
        "inputs": call["inputs"],
        "prompt": call["prompt"],
        "schema": call["schema"].model_json_schema(),
        "system_prompt": call["system_prompt"],
    }
    assert reserved == [HAIKU.worst_case(canonical(request), 256)]


async def test_cost_above_the_reservation_raises(postgres_engine, tmp_path):
    api = FakeApi(message(input_tokens=10_000_000, output_tokens=9))
    with pytest.raises(UsageError):
        await gateway(api, postgres_engine, tmp_path).call(**choose())


def test_missing_key_refuses_to_build():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicTransport(settings(""), 256)


def test_schema_helper_closes_nested_objects():
    from docket.llm.anthropic import strict_schema

    assert strict_schema(Classification)["additionalProperties"] is False
    assert "$defs" not in json.dumps(strict_schema(choice_schema(["UNIT-0001"])))
