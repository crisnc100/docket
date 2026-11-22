"""The hosted beta's sample documents: the caps, the kill switch and the daily headroom."""

import hashlib
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import Date, Engine, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from docket.config import load_records
from docket.db import BetaFlag, BudgetLedger, SampleCount, SampleSend
from docket.llm.budget import PriceCeiling, load_prices, money
from docket.llm.gateway import MAX_OUTPUT_TOKENS, model_request
from docket.llm.replay import canonical
from docket.prompts import choose_request, classify_request
from docket.settings import Settings
from docket.sweep import sweep_stale

ADDRESS_LIMIT = 20
ADDRESS_WINDOW = 3600
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
}

TURNED_OFF = "Live answers are turned off right now."
# The pause follows the daily ledger, whose day is the UTC day.
PAUSED = "Live answers are paused for today. Try again tomorrow."
DAY_CAPPED = f"Today's {DAILY_LIMIT} samples have all been sent. Try again tomorrow."


def address_capped(seconds: int) -> str:
    minutes = math.ceil(seconds / 60)
    wait = "1 minute" if minutes == 1 else f"{minutes} minutes"
    return f"You can send {ADDRESS_LIMIT} samples an hour. Try again in {wait}."


type SampleState = Literal["ready", "off", "paused", "catching_up"]


def flag(engine: Engine, name: str) -> bool | None:
    with Session(engine) as session:
        return session.scalar(select(BetaFlag.value).where(BetaFlag.name == name))


def set_flag(engine: Engine, name: str, value: bool) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            insert(BetaFlag)
            .values(name=name, value=value)
            .on_conflict_do_update(
                index_elements=[BetaFlag.name], set_={"value": value, "updated_at": func.now()}
            )
        )


def live_answers_on(engine: Engine) -> bool:
    return flag(engine, LIVE_ANSWERS) is not False


def set_live_answers(engine: Engine, on: bool) -> None:
    set_flag(engine, LIVE_ANSWERS, on)


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
        if sweep_stale(self.engine):
            return "catching_up"
        if not live_answers_on(self.engine):
            return "off"
        limit = Decimal(str(self.settings.docket_daily_budget_usd))
        if headroom(self.engine, limit) < self.ceiling:
            return "paused"
        return "ready"


def _address_lock(address_hash: str) -> int:
    return int.from_bytes(
        hashlib.sha256(b"sample:" + address_hash.encode()).digest()[:8], signed=True
    )


def take_slot(session: Session, address_hash: str) -> tuple[str, int] | None:
    """Count one sample against the address's last 60 minutes and against the UTC day, or
    against neither, inside the caller's transaction.

    The address cap counts rows under a lock for that address, so parallel requests never
    pass it and the window rolls rather than resetting on the hour. The day cap is one
    conditional upsert. Returns None when the sample may go ahead, or else the refusal
    message and the seconds until a slot frees up; on a refusal the caller rolls back, so
    neither cap counts it. The time comes from the database's clock, so every API process
    uses the same window."""
    window_start = func.now() - timedelta(seconds=ADDRESS_WINDOW)
    session.execute(select(func.pg_advisory_xact_lock(_address_lock(address_hash))))
    session.execute(
        delete(SampleSend).where(
            SampleSend.address_hash == address_hash, SampleSend.at <= window_start
        )
    )
    recent = session.scalar(select(func.count()).where(SampleSend.address_hash == address_hash))
    if recent is not None and recent >= ADDRESS_LIMIT:
        oldest = select(func.min(SampleSend.at)).where(SampleSend.address_hash == address_hash)
        seconds = session.scalar(
            select(func.extract("epoch", oldest.scalar_subquery() - window_start))
        )
        wait = max(1, math.ceil(seconds or 1))
        return address_capped(wait), wait
    day = cast(func.timezone("UTC", func.now()), Date)
    counted = session.execute(
        insert(SampleCount)
        .values(day=day, count=1)
        .on_conflict_do_update(
            index_elements=[SampleCount.day],
            set_={"count": SampleCount.count + 1},
            where=SampleCount.count < DAILY_LIMIT,
        )
        .returning(SampleCount.count)
    ).scalar_one_or_none()
    if counted is None:
        midnight = func.date_trunc("day", func.now(), "UTC") + timedelta(days=1)
        seconds = session.scalar(select(func.extract("epoch", midnight - func.now())))
        return DAY_CAPPED, max(1, math.ceil(seconds or 1))
    session.execute(insert(SampleSend).values(address_hash=address_hash))
    return None
