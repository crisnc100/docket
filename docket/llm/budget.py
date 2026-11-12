from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from threading import Lock

import yaml
from sqlalchemy import Engine, update
from sqlalchemy.dialects.postgresql import insert

from docket.db import BudgetLedger
from docket.llm.errors import BudgetExceeded, UsageError

ZERO = Decimal("0")
QUANTUM = Decimal("0.000000001")
PRICES_PATH = Path("config/model_prices.yaml")


def money(value: Decimal) -> Decimal:
    if not value.is_finite() or value < ZERO:
        raise ValueError("Cost and budget must be finite and non-negative")
    return value.quantize(QUANTUM, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class PriceCeiling:
    prompt_per_million: Decimal = Decimal("0.30")
    completion_per_million: Decimal = Decimal("1.20")
    long_prompt_tokens: int | None = None
    long_prompt_per_million: Decimal | None = None
    long_completion_per_million: Decimal | None = None

    def __post_init__(self):
        money(self.prompt_per_million)
        money(self.completion_per_million)
        tier = (
            self.long_prompt_tokens,
            self.long_prompt_per_million,
            self.long_completion_per_million,
        )
        if any(item is not None for item in tier):
            if self.long_prompt_tokens is None or self.long_prompt_tokens < 1:
                raise ValueError("A long-prompt tier needs a positive token line")
            if self.long_prompt_per_million is None or self.long_completion_per_million is None:
                raise ValueError("A long-prompt tier needs both prices")
            money(self.long_prompt_per_million)
            money(self.long_completion_per_million)

    def _rates(self, input_tokens: int) -> tuple[Decimal, Decimal]:
        if (
            self.long_prompt_tokens is not None
            and self.long_prompt_per_million is not None
            and self.long_completion_per_million is not None
            and input_tokens > self.long_prompt_tokens
        ):
            return self.long_prompt_per_million, self.long_completion_per_million
        return self.prompt_per_million, self.completion_per_million

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts must be non-negative")
        prompt, completion = self._rates(input_tokens)
        return money((input_tokens * prompt + output_tokens * completion) / Decimal(1_000_000))

    def worst_case(self, serialized_request: str, max_output_tokens: int) -> Decimal:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        # A token is at least one UTF-8 byte, so the byte count bounds the input tokens.
        # The 4096 extra leaves room for what the provider adds around the schema and prompt.
        input_bound = len(serialized_request.encode("utf-8")) + 4096
        return self.cost(input_bound, max_output_tokens)


@dataclass(frozen=True)
class PriceSnapshot:
    snapshot_date: str
    source: str
    models: dict[str, PriceCeiling]

    def ceiling(self, model: str) -> PriceCeiling:
        if model not in self.models:
            raise KeyError(f"The price snapshot has no price for {model}")
        return self.models[model]


def load_prices(path: Path = PRICES_PATH) -> PriceSnapshot:
    data = yaml.safe_load(path.read_text())
    try:
        models = {
            name: PriceCeiling(
                Decimal(entry["input_per_million"]),
                Decimal(entry["output_per_million"]),
                entry.get("long_prompt_tokens"),
                _optional_price(entry.get("long_input_per_million")),
                _optional_price(entry.get("long_output_per_million")),
            )
            for name, entry in data["models"].items()
        }
        return PriceSnapshot(str(data["snapshot_date"]), str(data["source"]), models)
    except (KeyError, TypeError, AttributeError, InvalidOperation) as exc:
        raise ValueError(f"Malformed price snapshot: {path}") from exc


def _optional_price(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class RunBudget:
    """Share one instance across all gateways for a run."""

    def __init__(self, limit: Decimal):
        self.limit = money(limit)
        self.spent = ZERO
        self._lock = Lock()

    def reserve(self, amount: Decimal) -> None:
        with self._lock:
            if self.spent + amount > self.limit:
                raise BudgetExceeded("Per-run budget exceeded before model call")
            self.spent += amount

    def settle(self, reserved: Decimal, actual: Decimal) -> None:
        with self._lock:
            self.spent += actual - reserved


class DailyBudget:
    def __init__(self, engine: Engine, limit: Decimal):
        self.engine = engine
        self.limit = money(limit)

    def reserve(self, amount: Decimal) -> date:
        day = datetime.now(UTC).date()
        with self.engine.begin() as connection:
            connection.execute(
                insert(BudgetLedger)
                .values(day=day, spent_usd=ZERO)
                .on_conflict_do_nothing(index_elements=[BudgetLedger.day])
            )
            result = connection.execute(
                update(BudgetLedger)
                .where(BudgetLedger.day == day, BudgetLedger.spent_usd + amount <= self.limit)
                .values(spent_usd=BudgetLedger.spent_usd + amount)
                .returning(BudgetLedger.day)
            ).scalar_one_or_none()
            if result is None:
                raise BudgetExceeded("Global daily budget exceeded before model call")
        return day

    def settle(self, day: date, reserved: Decimal, actual: Decimal) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(BudgetLedger)
                .where(BudgetLedger.day == day)
                .values(spent_usd=BudgetLedger.spent_usd + actual - reserved)
            )


class Budget:
    def __init__(self, run: RunBudget, daily: DailyBudget):
        self.run = run
        self.daily = daily

    def reserve(self, amount: Decimal) -> date:
        amount = money(amount)
        self.run.reserve(amount)
        try:
            return self.daily.reserve(amount)
        except Exception:
            self.run.settle(amount, ZERO)
            raise

    def settle(self, day: date, reserved: Decimal, actual: Decimal) -> None:
        actual = money(actual)
        self.daily.settle(day, reserved, actual)
        self.run.settle(reserved, actual)
        if actual > reserved:
            raise UsageError("Provider cost exceeded the reserved price ceiling")
