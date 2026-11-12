import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from openai import (
    APIResponseValidationError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
)
from pydantic import BaseModel, ValidationError

from docket.llm.anthropic import Reply
from docket.llm.budget import Budget, PriceCeiling
from docket.llm.errors import (
    ExportPending,
    MaybeBilled,
    NoAnswer,
    NotSent,
    ParseError,
    RecordRefused,
    UnreadableAnswer,
    UsageError,
)
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
        exchange: Exchange | None = None,
        provider: str = "openrouter",
        today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat(),
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
        self.provider = provider
        self.today = today

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

        def pending() -> PendingRequest:
            return PendingRequest(
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

        if self.mode == Mode.EXPORT:
            self.exchange.export(pending())
            raise ExportPending(f"Request exported: {key}")
        if self.mode == Mode.IMPORT:
            self.exchange.import_answers([key], self.store)
        if self.mode in {Mode.REPLAY, Mode.IMPORT}:
            try:
                entry = self.store.get(key)
                self.used_recordings[key] = entry
                if entry.outcome == "unreadable":
                    raise UnreadableAnswer(entry.reason or "")
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
                        self.actual_cost_usd += cost
                        self.budget.settle(day, worst, cost)
                raise ParseError("Model output does not match the per-call schema") from exc
            except MaybeBilled:
                # The reservation stays: the call is charged at its worst case.
                self.actual_cost_usd += worst
                raise
            except Exception as exc:
                if isinstance(exc, NotSent) or getattr(exc, "status_code", None) == 429:
                    self.budget.settle(day, worst, Decimal(0))
                    if attempt < 4:
                        await self.sleep(2**attempt)
                        continue
                    if isinstance(exc, NotSent):
                        raise NoAnswer(f"No answer after 4 retries: {exc}") from exc
                # Ambiguous transport failures retain reservations: the provider may have billed.
                raise
            if isinstance(result, Reply):
                return self._settle_reply(
                    result,
                    schema,
                    key=key,
                    source=source,
                    budget=self.budget,
                    day=day,
                    worst=worst,
                    pending=pending,
                )
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

    def _settle_reply[T: BaseModel](
        self,
        reply: Reply,
        schema: type[T],
        *,
        key: str,
        source: str,
        budget: Budget,
        day: Any,
        worst: Decimal,
        pending: Callable[[], PendingRequest],
    ) -> T:
        cost = self.prices.cost(reply.input_tokens, reply.output_tokens)
        self.actual_cost_usd += cost
        # Charged before parsing: the API bills unreadable answers too.
        budget.settle(day, worst, cost)
        reason, output = read_reply(reply, schema)
        if source == "synthetic_corpus":
            entry = Recording(
                key=key,
                outcome="unreadable" if reason else "answer",
                reason=reason,
                output=None if output is None else output.model_dump(mode="json"),
                raw_text=reply.text,
                cost_usd=str(cost),
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                latency_ms=reply.latency_ms,
                output_mode=reply.output_mode,
                source=source,
                provenance={
                    "provider": self.provider,
                    "model": self.model,
                    "recorded": self.today(),
                },
            )
            self.used_recordings[key] = entry
            if self.mode == Mode.RECORD:
                # The request text goes beside the answer, for packing into the exchange file.
                self.exchange.export(pending())
                self.store.append(entry)
        if reason:
            raise UnreadableAnswer(reason)
        assert output is not None
        return output


def read_reply[T: BaseModel](reply: Reply, schema: type[T]) -> tuple[str | None, T | None]:
    if reply.stop_reason == "refusal":
        return "refusal", None
    if reply.stop_reason in {"max_tokens", "model_context_window_exceeded"}:
        return "cut", None
    try:
        value = json.loads(reply.text)
    except (ValueError, RecursionError):
        return "not_json", None
    if not isinstance(value, dict):
        return "not_json", None
    try:
        return None, schema.model_validate(value)
    except ValidationError:
        return "schema", None
