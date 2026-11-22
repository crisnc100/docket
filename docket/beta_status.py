"""`docket beta-status`: what the operator watches, from Postgres alone. It prints counts and
ages, never an id, a hash or a visitor's text."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from docket.beta_samples import ADDRESS_LIMIT, DAILY_LIMIT, live_answers_on
from docket.db import BudgetLedger, Document, SampleCount, SampleSend, Sandbox, SandboxCreation
from docket.sandbox import CREATION_LIMIT, CREATION_WINDOW
from docket.settings import Settings
from docket.sweep import STALE_AFTER, sweep_age


def _at_limit(session: Session, table, limit: int) -> tuple[int, int]:
    """Rows in the last hour, and how many addresses have reached the limit, so a shared
    office address that keeps hitting it shows."""
    window_start = func.now() - CREATION_WINDOW
    per_address = (
        select(func.count().label("sent"))
        .where(table.at > window_start)
        .group_by(table.address_hash)
        .subquery()
    )
    total = session.scalar(select(func.coalesce(func.sum(per_address.c.sent), 0)))
    capped = session.scalar(select(func.count()).where(per_address.c.sent >= limit))
    return int(total or 0), int(capped or 0)


def status_lines(engine: Engine, settings: Settings) -> list[str]:
    age = sweep_age(engine)
    if age is None:
        sweep = "Last complete sweep: never (stale: samples are refused)"
    else:
        state = "stale: samples are refused" if age > STALE_AFTER else "ok"
        sweep = f"Last complete sweep: {age:.0f} seconds ago ({state})"
    today = datetime.now(UTC).date()
    with Session(engine) as session:
        live = session.scalar(
            select(func.count()).where(Sandbox.closed_at.is_(None), Sandbox.expires_at > func.now())
        )
        waiting = (session.scalar(select(func.count()).select_from(Sandbox)) or 0) - (live or 0)
        oldest = session.scalar(
            select(func.extract("epoch", func.now() - func.min(Sandbox.expires_at))).where(
                Sandbox.expires_at <= func.now()
            )
        )
        in_sandboxes = session.scalar(select(func.count()).where(Document.sandbox_id.is_not(None)))
        loose = session.scalar(select(func.count()).where(Document.sandbox_id.is_(None)))
        created, creation_capped = _at_limit(session, SandboxCreation, CREATION_LIMIT)
        sent, sample_capped = _at_limit(session, SampleSend, ADDRESS_LIMIT)
        day_count = session.scalar(select(SampleCount.count).where(SampleCount.day == today))
        spent = session.scalar(select(BudgetLedger.spent_usd).where(BudgetLedger.day == today))
    expired_for = (
        f"; the oldest expired {timedelta(seconds=int(oldest))} ago" if oldest is not None else ""
    )
    budget = Decimal(str(settings.docket_daily_budget_usd))
    return [
        sweep,
        f"Sandboxes: {live} live, {waiting} waiting for the sweep{expired_for}",
        f"Documents: {in_sandboxes} in sandboxes, {loose} outside any sandbox",
        f"New sandboxes in the last hour: {created}; "
        f"addresses at the limit of {CREATION_LIMIT}: {creation_capped}",
        f"Samples in the last hour: {sent}; addresses at the limit of {ADDRESS_LIMIT}: "
        f"{sample_capped}; today: {day_count or 0} of {DAILY_LIMIT}",
        f"Live answers: {'on' if live_answers_on(engine) else 'off'}; "
        f"spent today: ${spent or Decimal(0):.4f} of ${budget:.2f}",
    ]
