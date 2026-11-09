import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import httpx
import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import select

from docket.db import BudgetLedger
from docket.llm.budget import Budget, DailyBudget, PriceCeiling, RunBudget
from docket.llm.errors import BudgetExceeded, ParseError, RecordRefused, ReplayMiss, UsageError
from docket.llm.gateway import Gateway, Mode, OpenRouterTransport
from docket.llm.replay import ReplayStore, replay_key
from docket.settings import Settings


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: Literal["UNIT-0001", "none"]


def request():
    return dict(
        activity="choose",
        prompt_version="v1",
        inputs={"text": "synthetic"},
        prompt="Choose UNIT-0001 or none",
        schema=Choice,
        source="synthetic_corpus",
    )


def budget(engine, run="1", daily="1"):
    return Budget(RunBudget(Decimal(run)), DailyBudget(engine, Decimal(daily)))


class Transport:
    def __init__(self, content='{"record_id":"UNIT-0001"}', parsed=None, cost="0.0001"):
        self.content = content
        self.parsed = parsed
        self.cost = cost
        self.calls = 0

    async def __call__(self, prompt, schema):
        self.calls += 1
        return {
            "raw": AIMessage(
                content=self.content, response_metadata={"token_usage": {"cost": self.cost}}
            ),
            "parsed": self.parsed,
        }


def spent(engine):
    with engine.connect() as connection:
        return connection.execute(select(BudgetLedger.spent_usd)).scalar_one()


def test_replay_key_normalizes_and_separates_contract():
    key = replay_key("choose", "v1", "m1", {"z": "é\r\n", "a": 1})
    assert key == replay_key("choose", "v1", "m1", {"a": 1, "z": "e\u0301\n"})
    assert key != replay_key("choose", "v2", "m1", {"z": "é\n", "a": 1})
    assert key != replay_key("classify", "v1", "m1", {"z": "é\n", "a": 1})
    assert key != replay_key("choose", "v1", "m2", {"z": "é\n", "a": 1})
    assert key != replay_key("choose", "v1", "m1", {"z": "é\n", "a": 2})


async def test_record_then_replay_without_key_or_network(postgres_engine, tmp_path):
    store = ReplayStore(tmp_path / "calls.jsonl")
    transport = Transport()
    run_budget = budget(postgres_engine)
    gateway = Gateway(
        mode=Mode.RECORD, model="m1", store=store, budget=run_budget, transport=transport
    )
    assert (await gateway.call(**request())).record_id == "UNIT-0001"
    assert transport.calls == gateway.call_count == 1
    assert spent(postgres_engine) == run_budget.run.spent == Decimal("0.0001")
    replay = Gateway(mode=Mode.REPLAY, model="m1", store=store)
    assert await replay.call(**request()) == Choice(record_id="UNIT-0001")
    assert replay.call_count == 0
    assert len(store.path.read_text().splitlines()) == 1
    assert "synthetic" not in json.loads(store.path.read_text())["output"]


@pytest.mark.parametrize("change", [{}, {"prompt_version": "v2"}, {"model": "other"}])
async def test_replay_miss_non_retryable(tmp_path, change):
    model = change.pop("model", "m1")
    gateway = Gateway(mode=Mode.REPLAY, model=model, store=ReplayStore(tmp_path / "missing"))
    with pytest.raises(ReplayMiss) as error:
        await gateway.call(**(request() | change))
    assert error.value.non_retryable
    assert gateway.call_count == 0


@pytest.mark.parametrize("source", ["external", "email", "", "synthetic", "synthetic_corpus "])
async def test_record_refuses_non_corpus_before_call(tmp_path, source):
    transport = Transport()
    gateway = Gateway(
        mode=Mode.RECORD, model="m1", store=ReplayStore(tmp_path / "calls"), transport=transport
    )
    with pytest.raises(RecordRefused) as error:
        await gateway.call(**(request() | {"source": source}))
    assert error.value.non_retryable
    assert transport.calls == 0
    assert not gateway.store.path.exists()


@pytest.mark.parametrize("run,daily", [("0.000001", "1"), ("1", "0.000001")])
async def test_budget_precheck_prevents_call(postgres_engine, tmp_path, run, daily):
    transport = Transport()
    cap = budget(postgres_engine, run, daily)
    gateway = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=ReplayStore(tmp_path / "calls"),
        transport=transport,
        budget=cap,
    )
    with pytest.raises(BudgetExceeded) as error:
        await gateway.call(**request())
    assert error.value.non_retryable
    assert transport.calls == gateway.call_count == 0
    assert cap.run.spent == 0


def test_daily_cap_survives_instances_and_serializes_concurrency(postgres_engine):
    def reserve(_):
        try:
            DailyBudget(postgres_engine, Decimal("0.1")).reserve(Decimal("0.03"))
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))
    assert sum(results) == 3
    assert spent(postgres_engine) == Decimal("0.09")
    with pytest.raises(BudgetExceeded):
        DailyBudget(postgres_engine, Decimal("0.1")).reserve(Decimal("0.02"))


def test_budget_reservation_settles_original_day(postgres_engine):
    cap = budget(postgres_engine)
    day = cap.reserve(Decimal("0.2"))
    assert day == datetime.now(UTC).date()
    cap.settle(day, Decimal("0.2"), Decimal("0.01"))
    assert spent(postgres_engine) == cap.run.spent == Decimal("0.01")


@pytest.mark.parametrize(
    "content", ['{"record_id":"UNIT-9999"}', "not json", '{"record_id":"none","extra":true}']
)
async def test_parse_error_is_typed_and_charged(postgres_engine, tmp_path, content):
    cap = budget(postgres_engine)
    gateway = Gateway(
        mode=Mode.RECORD,
        model="m1",
        store=ReplayStore(tmp_path / "calls"),
        budget=cap,
        transport=Transport(content=content),
    )
    with pytest.raises(ParseError) as error:
        await gateway.call(**request())
    assert error.value.non_retryable
    assert spent(postgres_engine) == Decimal("0.0001")
    assert not gateway.store.path.exists()


async def test_structured_output_is_revalidated(postgres_engine, tmp_path):
    gateway = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=ReplayStore(tmp_path / "calls"),
        budget=budget(postgres_engine),
        transport=Transport(parsed={"record_id": "outside"}),
    )
    with pytest.raises(ParseError):
        await gateway.call(**request())


@pytest.mark.parametrize("cost", [None, "NaN", "-1", "invalid"])
async def test_invalid_usage_retains_worst_case_reservation(postgres_engine, tmp_path, cost):
    cap = budget(postgres_engine)
    gateway = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=ReplayStore(tmp_path / "calls"),
        budget=cap,
        transport=Transport(cost=cost),
    )
    with pytest.raises(UsageError):
        await gateway.call(**request())
    assert spent(postgres_engine) == cap.run.spent > 0


async def test_ambiguous_failure_retains_reservation(postgres_engine, tmp_path):
    async def fail(prompt, schema):
        raise TimeoutError("may have been billed")

    cap = budget(postgres_engine)
    gateway = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=ReplayStore(tmp_path / "calls"),
        budget=cap,
        transport=fail,
    )
    with pytest.raises(TimeoutError):
        await gateway.call(**request())
    assert spent(postgres_engine) == cap.run.spent > 0


async def test_429_backoff_is_bounded(postgres_engine, tmp_path):
    class RateLimited(Exception):
        status_code = 429

    delays = []

    async def sleep(delay):
        delays.append(delay)

    async def fail(prompt, schema):
        raise RateLimited()

    cap = budget(postgres_engine)
    gateway = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=ReplayStore(tmp_path / "calls"),
        budget=cap,
        transport=fail,
        sleep=sleep,
    )
    with pytest.raises(RateLimited):
        await gateway.call(**request())
    assert delays == [1, 2, 4, 8]
    assert gateway.call_count == 5
    assert spent(postgres_engine) == cap.run.spent == 0


async def test_openrouter_wire_schema_provider_usage_and_fallback(postgres_engine, tmp_path):
    captured = []

    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "generation-test",
                "object": "chat.completion",
                "created": 1,
                "model": "openai/gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"record_id":"UNIT-0001"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "total_tokens": 28,
                    "cost": 0.00001,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        settings = Settings(_env_file=None, openrouter_api_key=SecretStr("test-key"))
        transport = OpenRouterTransport(settings, PriceCeiling(), 64, client)
        gateway = Gateway(
            mode=Mode.LIVE,
            model=settings.docket_model,
            store=ReplayStore(tmp_path / "calls"),
            budget=budget(postgres_engine),
            transport=transport,
            max_output_tokens=64,
        )
        assert (await gateway.call(**request())).record_id == "UNIT-0001"
    body = captured[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["properties"]["record_id"]["enum"] == [
        "UNIT-0001",
        "none",
    ]
    assert body["provider"]["only"] == ["OpenAI"]
    assert body["provider"]["allow_fallbacks"] is False
    assert body["provider"]["require_parameters"] is True
    assert body["provider"]["max_price"] == {"prompt": 0.3, "completion": 1.2, "request": 0}
    assert body["max_completion_tokens"] == 64
    assert spent(postgres_engine) == Decimal("0.00001")


def test_worst_case_includes_utf8_and_completion_ceiling():
    ceiling = PriceCeiling(Decimal("1"), Decimal("2"))
    assert ceiling.worst_case("é", 10) == Decimal("0.004118")
    with pytest.raises(ValueError):
        ceiling.worst_case("", 0)
    with pytest.raises(ValueError):
        RunBudget(Decimal("NaN"))


async def test_populated_replay_misses_on_changed_contract(postgres_engine, tmp_path):
    store = ReplayStore(tmp_path / "calls")
    recorder = Gateway(
        mode=Mode.RECORD,
        model="m1",
        store=store,
        budget=budget(postgres_engine),
        transport=Transport(),
    )
    await recorder.call(**request())
    replay = Gateway(mode=Mode.REPLAY, model="m1", store=store)
    for change in [
        {"prompt_version": "v2"},
        {"activity": "classify"},
        {"prompt": "Changed instruction"},
        {"inputs": {"text": "changed"}},
    ]:
        with pytest.raises(ReplayMiss):
            await replay.call(**(request() | change))
    other_model = Gateway(mode=Mode.REPLAY, model="m2", store=store)
    with pytest.raises(ReplayMiss):
        await other_model.call(**request())
    assert replay.call_count == other_model.call_count == 0


async def test_429_then_success_only_charges_success(postgres_engine, tmp_path):
    class RateLimited(Exception):
        status_code = 429

    calls = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    async def transport(prompt, schema):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimited()
        return await Transport()(prompt, schema)

    gateway = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=ReplayStore(tmp_path / "calls"),
        budget=budget(postgres_engine),
        transport=transport,
        sleep=sleep,
    )
    assert (await gateway.call(**request())).record_id == "UNIT-0001"
    assert delays == [1]
    assert gateway.call_count == 2
    assert spent(postgres_engine) == Decimal("0.0001")


async def test_run_budget_is_shared_across_gateways(postgres_engine, tmp_path):
    shared_run = RunBudget(Decimal("0.002"))
    store = ReplayStore(tmp_path / "calls")
    first = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=store,
        budget=Budget(shared_run, DailyBudget(postgres_engine, Decimal("1"))),
        transport=Transport(cost="0.001"),
    )
    await first.call(**request())
    second_transport = Transport()
    second = Gateway(
        mode=Mode.LIVE,
        model="m1",
        store=store,
        budget=Budget(shared_run, DailyBudget(postgres_engine, Decimal("1"))),
        transport=second_transport,
    )
    with pytest.raises(BudgetExceeded):
        await second.call(**request())
    assert shared_run.spent == Decimal("0.001")
    assert second_transport.calls == 0
