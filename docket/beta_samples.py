"""The hosted beta's sample documents: the caps, the kill switch and the daily headroom."""

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import Engine, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from docket.config import load_records
from docket.db import BetaFlag, BudgetLedger, SampleCount
from docket.llm.budget import PriceCeiling, load_prices, money
from docket.llm.gateway import MAX_OUTPUT_TOKENS, model_request
from docket.llm.replay import canonical
from docket.prompts import choose_request, classify_request
from docket.settings import Settings

ADDRESS_HOURLY_LIMIT = 20
DAILY_LIMIT = 300
LIVE_ANSWERS = "live_answers"
SAMPLE_SENDER = "Sample generator"
SAMPLE_LABELS = {
    "clean": "Every clue agrees",
    "id_in_filename_only": "Unit number only in the file name",
    "address_only": "Address only",
    "tenant_only": "Tenant name only",
    "former_tenant": "A former tenant's name",
    "two_record_conflict": "Two units named",
    "decoy_near_miss_address": "Misspelled address, neighbour named",
    "building_level": "Whole building, no unit",
    "no_record": "Address not on file",
    "injection": "Hidden instructions to the model",
    "very_long": "Very long report",
    "graded_evidence": "Partial or slightly wrong clues",
}

TURNED_OFF = "Live model answers are turned off right now."
PAUSED = "Live answers are paused for today."
ADDRESS_CAPPED = f"You can send {ADDRESS_HOURLY_LIMIT} samples an hour. Try again later."
DAY_CAPPED = f"Today's {DAILY_LIMIT} samples have all been sent. Try again tomorrow."

type SampleState = Literal["ready", "off", "paused"]


def live_answers_on(engine: Engine) -> bool:
    with Session(engine) as session:
        value = session.scalar(select(BetaFlag.value).where(BetaFlag.name == LIVE_ANSWERS))
    return value is not False


def set_live_answers(engine: Engine, on: bool) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            insert(BetaFlag)
            .values(name=LIVE_ANSWERS, value=on)
            .on_conflict_do_update(
                index_elements=[BetaFlag.name], set_={"value": on, "updated_at": func.now()}
            )
        )


def document_ceiling(prices: PriceCeiling, record_ids: list[str]) -> Decimal:
    """The most one document can reserve at once: the classify and choose calls for the
    longest text the model is given (8,000 characters, at 4 UTF-8 bytes each), with every
    record as a candidate. Retries reserve again only after giving back the last attempt."""
    text = "\U0010ffff" * 8000
    calls = [classify_request(text), choose_request(text, record_ids)]
    return sum(
        (
            prices.worst_case(
                canonical(
                    model_request(
                        inputs=call["inputs"],
                        prompt=call["prompt"],
                        schema=call["schema"],
                        system_prompt=call["system_prompt"],
                    )
                ),
                MAX_OUTPUT_TOKENS,
            )
            for call in calls
        ),
        Decimal(0),
    )


def headroom(engine: Engine, limit: Decimal) -> Decimal:
    """What today's ledger can still reserve. The day is the one DailyBudget.reserve uses."""
    day = datetime.now(UTC).date()
    with Session(engine) as session:
        spent = session.scalar(select(BudgetLedger.spent_usd).where(BudgetLedger.day == day))
    return money(limit) - (spent or Decimal(0))


class SampleGate:
    """Whether a sample may start now. The price ceiling is worked out once per process."""

    def __init__(self, engine: Engine, settings: Settings):
        self.engine, self.settings = engine, settings
        self.ceiling = document_ceiling(
            load_prices().ceiling(settings.docket_model),
            [record.record_id for record in load_records()],
        )

    def state(self) -> SampleState:
        if not live_answers_on(self.engine):
            return "off"
        limit = Decimal(str(self.settings.docket_daily_budget_usd))
        if headroom(self.engine, limit) < self.ceiling:
            return "paused"
        return "ready"


def take_slot(engine: Engine, address: str) -> tuple[str, int] | None:
    """Count one sample against the address's hour and the whole day, or against neither.

    Each count is one conditional upsert, so parallel requests never pass a limit. Both run
    in one transaction, so a refusal on either gives the other back. Returns None when the
    sample may go ahead, or else the refusal message and the seconds until its window ends."""
    with Session(engine) as session:
        for scope, unit, limit, message in (
            ("address:" + address, "hour", ADDRESS_HOURLY_LIMIT, ADDRESS_CAPPED),
            ("day", "day", DAILY_LIMIT, DAY_CAPPED),
        ):
            window = func.date_trunc(unit, func.now(), "UTC")
            counted = session.execute(
                insert(SampleCount)
                .values(scope=scope, window=window, count=1)
                .on_conflict_do_update(
                    index_elements=[SampleCount.scope, SampleCount.window],
                    set_={"count": SampleCount.count + 1},
                    where=SampleCount.count < limit,
                )
                .returning(SampleCount.count)
            ).scalar_one_or_none()
            if counted is None:
                ends = window + literal_column(f"interval '1 {unit}'")
                seconds = session.scalar(select(func.extract("epoch", ends - func.now())))
                session.rollback()
                return message, max(1, math.ceil(seconds or 1))
        session.commit()
    return None
