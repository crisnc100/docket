import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from openai import (
    APIResponseValidationError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
)
from pydantic import BaseModel, ValidationError

from docket.llm.budget import Budget, PriceCeiling
from docket.llm.errors import ParseError, RecordRefused, UsageError
from docket.llm.replay import Recording, ReplayStore, canonical, replay_key
from docket.settings import Settings


class Mode(StrEnum):
    RECORD = "record"
    REPLAY = "replay"
    LIVE = "live"


class OpenRouterTransport:
    def __init__(
        self,
        settings: Settings,
        prices: PriceCeiling,
        max_output_tokens: int,
        http_client: Any = None,
    ):
        if not settings.openrouter_api_key.get_secret_value():
            raise ValueError("OPENROUTER_API_KEY is required for live calls")
        self.client = ChatOpenAI(
            model=settings.docket_model,
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
            temperature=0,
            max_completion_tokens=max_output_tokens,
            max_retries=0,
            timeout=30,
            http_async_client=http_client,
            use_responses_api=False,
            extra_body={
                "provider": {
                    "only": [settings.docket_provider],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                    "max_price": {
                        "prompt": float(prices.prompt_per_million),
                        "completion": float(prices.completion_per_million),
                        "request": 0,
                    },
                },
                "usage": {"include": True},
            },
        )

    async def __call__(self, prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
        chain = self.client.with_structured_output(
            schema.model_json_schema(), method="json_schema", strict=True, include_raw=True
        )
        return await chain.ainvoke(prompt)


def usage_cost(usage: Any) -> Decimal:
    try:
        cost = Decimal(str(usage["cost"]))
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise UsageError("OpenRouter usage is missing a valid cost") from exc
    if not cost.is_finite() or cost < 0:
        raise UsageError("OpenRouter usage cost must be finite and non-negative")
    return cost


def response_cost(raw: Any) -> Decimal:
    try:
        usage = raw.response_metadata["token_usage"]
    except (AttributeError, KeyError, TypeError) as exc:
        raise UsageError("OpenRouter usage is missing a valid cost") from exc
    return usage_cost(usage)


class Gateway:
    def __init__(
        self,
        *,
        mode: Mode,
        model: str,
        store: ReplayStore,
        budget: Budget | None = None,
        transport: Any = None,
        prices: PriceCeiling | None = None,
        max_output_tokens: int = 256,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.mode = Mode(mode)
        self.model = model
        self.store = store
        self.budget = budget
        self.transport = transport
        self.prices = prices or PriceCeiling()
        self.max_output_tokens = max_output_tokens
        self.sleep = sleep
        self.call_count = 0

    async def call[T: BaseModel](
        self,
        *,
        activity: str,
        prompt_version: str,
        inputs: Any,
        prompt: str,
        schema: type[T],
        source: str,
    ) -> T:
        if self.mode == Mode.RECORD and source != "synthetic_corpus":
            raise RecordRefused("Record mode accepts only the synthetic corpus")
        request = {"inputs": inputs, "prompt": prompt, "schema": schema.model_json_schema()}
        key = replay_key(activity, prompt_version, self.model, request)
        if self.mode == Mode.REPLAY:
            try:
                return schema.model_validate(self.store.get(key).output)
            except ValidationError as exc:
                raise ParseError("Replay output does not match the per-call schema") from exc
        if self.budget is None or self.transport is None:
            raise ValueError("Live and record calls require a budget and transport")
        worst = self.prices.worst_case(canonical(request), self.max_output_tokens)
        for attempt in range(5):
            day = self.budget.reserve(worst)
            self.call_count += 1
            try:
                result = await self.transport(prompt, schema)
            except (
                LengthFinishReasonError,
                ContentFilterFinishReasonError,
                APIResponseValidationError,
                OutputParserException,
                ValidationError,
            ) as exc:
                usage = getattr(getattr(exc, "completion", None), "usage", None)
                if usage is not None:
                    try:
                        cost = usage_cost(usage.model_dump())
                    except UsageError:
                        pass
                    else:
                        self.budget.settle(day, worst, cost)
                raise ParseError("Model output does not match the per-call schema") from exc
            except Exception as exc:
                if getattr(exc, "status_code", None) == 429:
                    self.budget.settle(day, worst, Decimal(0))
                    if attempt < 4:
                        await self.sleep(2**attempt)
                        continue
                # Ambiguous transport failures retain reservations: the provider may have billed.
                raise
            raw = result.get("raw")
            cost = response_cost(raw)
            self.budget.settle(day, worst, cost)
            try:
                parsed = result.get("parsed")
                if parsed is None:
                    parsed = schema.model_validate_json(raw.content)
                output = schema.model_validate(parsed)
            except (ValidationError, ValueError, TypeError, AttributeError) as exc:
                raise ParseError("Model output does not match the per-call schema") from exc
            if self.mode == Mode.RECORD:
                self.store.append(
                    Recording(
                        key=key,
                        output=output.model_dump(mode="json"),
                        cost_usd=str(cost),
                        source=source,
                    )
                )
            return output
        raise AssertionError("Unreachable retry state")
