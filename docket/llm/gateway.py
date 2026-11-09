import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from docket.llm.budget import Budget, PriceCeiling
from docket.llm.errors import ExportPending, ParseError, RecordRefused, UsageError
from docket.llm.exchange import Exchange, PendingRequest
from docket.llm.replay import Recording, ReplayStore, canonical, replay_key
from docket.settings import Settings


class Mode(StrEnum):
    RECORD = "record"
    REPLAY = "replay"
    LIVE = "live"
    EXPORT = "export"
    IMPORT = "import"


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

    async def __call__(
        self, prompt: str, schema: type[BaseModel], *, system_prompt: str = ""
    ) -> dict[str, Any]:
        chain = self.client.with_structured_output(
            schema.model_json_schema(), method="json_schema", strict=True, include_raw=True
        )
        messages = [("system", system_prompt), ("human", prompt)] if system_prompt else prompt
        return await chain.ainvoke(messages)


def response_cost(raw: Any) -> Decimal:
    try:
        cost = Decimal(str(raw.response_metadata["token_usage"]["cost"]))
    except (AttributeError, KeyError, TypeError, InvalidOperation) as exc:
        raise UsageError("OpenRouter usage is missing a valid cost") from exc
    if not cost.is_finite() or cost < 0:
        raise UsageError("OpenRouter usage cost must be finite and non-negative")
    return cost


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
        exchange: Exchange | None = None,
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
        self.exchange = exchange or Exchange(Path("evals/pending"), Path("evals/answers"))
        self.used_recordings: dict[str, Recording] = {}
        self.actual_cost_usd = Decimal(0)

    async def call[T: BaseModel](
        self,
        *,
        activity: str,
        prompt_version: str,
        inputs: Any,
        prompt: str,
        schema: type[T],
        source: str,
        system_prompt: str = "",
    ) -> T:
        if self.mode in {Mode.RECORD, Mode.EXPORT, Mode.IMPORT} and source != "synthetic_corpus":
            raise RecordRefused("Record mode accepts only the synthetic corpus")
        request = {"inputs": inputs, "prompt": prompt, "schema": schema.model_json_schema()}
        if system_prompt:
            request["system_prompt"] = system_prompt
        key = replay_key(activity, prompt_version, self.model, request)
        if self.mode == Mode.EXPORT:
            self.exchange.export(
                PendingRequest(
                    key=key,
                    activity=activity,
                    prompt_version=prompt_version,
                    target_model=self.model,
                    system_prompt=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                    response_schema=schema.model_json_schema(),
                    inputs=inputs,
                    source=source,
                )
            )
            raise ExportPending(f"Request exported: {key}")
        if self.mode == Mode.IMPORT:
            self.exchange.import_answers([key], self.store)
        if self.mode in {Mode.REPLAY, Mode.IMPORT}:
            try:
                entry = self.store.get(key)
                self.used_recordings[key] = entry
                return schema.model_validate(entry.output)
            except ValidationError as exc:
                raise ParseError("Replay output does not match the per-call schema") from exc
        if self.budget is None or self.transport is None:
            raise ValueError("Live and record calls require a budget and transport")
        worst = self.prices.worst_case(canonical(request), self.max_output_tokens)
        for attempt in range(5):
            day = self.budget.reserve(worst)
            self.call_count += 1
            try:
                result = (
                    await self.transport(prompt, schema, system_prompt=system_prompt)
                    if system_prompt
                    else await self.transport(prompt, schema)
                )
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
            self.actual_cost_usd += cost
            self.budget.settle(day, worst, cost)
            try:
                parsed = result.get("parsed")
                if parsed is None:
                    parsed = schema.model_validate_json(raw.content)
                output = schema.model_validate(parsed)
            except (ValidationError, ValueError, TypeError, AttributeError) as exc:
                raise ParseError("Model output does not match the per-call schema") from exc
            if source == "synthetic_corpus":
                entry = Recording(
                    key=key,
                    output=output.model_dump(mode="json"),
                    cost_usd=str(cost),
                    source=source,
                )
                self.used_recordings[key] = entry
                if self.mode == Mode.RECORD:
                    self.store.append(entry)
            return output
        raise AssertionError("Unreachable retry state")
