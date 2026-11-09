from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal
from threading import Lock

from sqlalchemy import Engine, update
from sqlalchemy.dialects.postgresql import insert

from docket.db import BudgetLedger
from docket.llm.errors import BudgetExceeded, UsageError

ZERO = Decimal("0")
QUANTUM = Decimal("0.000000001")


def money(value: Decimal) -> Decimal:
    if not value.is_finite() or value < ZERO:
        raise ValueError("Cost and budget must be finite and non-negative")
    return value.quantize(QUANTUM, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class PriceCeiling:
    prompt_per_million: Decimal = Decimal("0.30")
    completion_per_million: Decimal = Decimal("1.20")

    def __post_init__(self):
        money(self.prompt_per_million)
        money(self.completion_per_million)

    def worst_case(self, serialized_request: str, max_output_tokens: int) -> Decimal:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        # UTF-8 bytes bound text tokens; reserve extra for provider/schema framing.
        input_bound = len(serialized_request.encode("utf-8")) + 4096
        return money(
            (
                input_bound * self.prompt_per_million
                + max_output_tokens * self.completion_per_million
            )
            / Decimal(1_000_000)
        )


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
