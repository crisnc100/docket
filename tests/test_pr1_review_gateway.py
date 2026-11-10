from decimal import Decimal
from typing import Literal

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from openai import APIResponseValidationError
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from docket.db import BudgetLedger
from docket.llm.budget import Budget, DailyBudget, PriceCeiling, RunBudget
from docket.llm.errors import ParseError
from docket.llm.gateway import Gateway, Mode, OpenRouterTransport
from docket.llm.replay import ReplayStore
from docket.settings import Settings


class Choice(BaseModel):
    record_id: Literal["UNIT-0001", "none"]


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
@pytest.mark.parametrize("usage_cost", [0.00001, None, -1])
async def test_sdk_output_failure_is_non_retryable_and_settles_known_usage(
    postgres_engine, tmp_path, finish_reason, usage_cost
):
    settings = Settings(_env_file=None, openrouter_api_key=SecretStr("test-key"))
    calls = []

    async def handler(request):
        calls.append(request)
        usage = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
        if usage_cost is not None:
            usage["cost"] = usage_cost
        return httpx.Response(
            200,
            json={
                "id": "synthetic-output-failure",
                "object": "chat.completion",
                "created": 1,
                "model": settings.docket_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"record_id":"UNIT-0001"}'},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            },
        )

    cap = Budget(RunBudget(Decimal(1)), DailyBudget(postgres_engine, Decimal(1)))
    store = ReplayStore(tmp_path / "calls.jsonl")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = Gateway(
            mode=Mode.RECORD,
            model=settings.docket_model,
            store=store,
            budget=cap,
            transport=OpenRouterTransport(settings, PriceCeiling(), 64, client),
            max_output_tokens=64,
        )
        with pytest.raises(ParseError) as error:
            await gateway.call(
                activity="choose",
                prompt_version="v1",
                inputs={"text": "synthetic"},
                prompt="Choose UNIT-0001 or none",
                schema=Choice,
                source="synthetic_corpus",
            )
    assert error.value.non_retryable and error.value.__cause__ is not None
    assert gateway.call_count == len(calls) == 1
    assert not store.path.exists()
    with postgres_engine.connect() as connection:
        daily = connection.execute(select(BudgetLedger.spent_usd)).scalar_one()
    assert daily == cap.run.spent
    if usage_cost == 0.00001:
        assert daily == Decimal(".00001")
    else:
        assert daily > Decimal(".00001")


@pytest.mark.parametrize("failure", ["sdk_schema", "parser"])
async def test_response_schema_failures_are_parse_errors(postgres_engine, tmp_path, failure):
    response = httpx.Response(200, request=httpx.Request("POST", "https://example.invalid"))
    cause = (
        APIResponseValidationError(response, {})
        if failure == "sdk_schema"
        else OutputParserException("Invalid structured output")
    )

    async def transport(prompt, schema):
        raise cause

    cap = Budget(RunBudget(Decimal(1)), DailyBudget(postgres_engine, Decimal(1)))
    gateway = Gateway(
        mode=Mode.LIVE,
        model="synthetic-test",
        store=ReplayStore(tmp_path / "calls"),
        budget=cap,
        transport=transport,
    )
    with pytest.raises(ParseError) as error:
        await gateway.call(
            activity="choose",
            prompt_version="v1",
            inputs={},
            prompt="Choose none",
            schema=Choice,
            source="synthetic_corpus",
        )
    assert error.value.non_retryable and error.value.__cause__ is cause
    assert gateway.call_count == 1
    assert cap.run.spent > 0
