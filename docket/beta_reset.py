"""The hosted beta's nightly reset: back to a starting queue of held seed-11 documents."""

import asyncio
import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from docket.beta_samples import RESET_LOCK, RESETTING, SAMPLE_SENDER, set_flag
from docket.core.ledger import TERMINAL_STATUSES
from docket.db import (
    BetaFlag,
    Document,
    SampleSend,
    SigninFailure,
)
from docket.ingress import Ingress
from docket.runtime import Runtime

RESET_HOUR_UTC = 8
CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "seed-11"
# Twelve seed-11 documents that the recorded live answers send to review, one or two of
# each kind of hold. Replayed, they cost nothing.
STARTING_QUEUE = (
    "doc-0002-UNIT-0200-id_in_filename_only.pdf",
    "doc-0003-address_only.pdf",
    "doc-0004-tenant_only.pdf",
    "doc-0005-former_tenant.pdf",
    "doc-0006-two_record_conflict.pdf",
    "doc-0007-decoy_near_miss_address.pdf",
    "doc-0008-building_level.pdf",
    "doc-0009-no_record.pdf",
    "doc-0010-injection.pdf",
    "doc-0017-very_long.pdf",
    "doc-0024-decoy_near_miss_address.pdf",
    "doc-0039-former_tenant.pdf",
)
# The pruned rows no longer count toward any limit: both windows are an hour.
COUNTER_WINDOW = timedelta(hours=1)
READY_FILE = Path("/tmp/docket-reset-ready")
RETRY_SECONDS = 300

log = logging.getLogger(__name__)


class IncompleteQueue(RuntimeError):
    """The reseeded documents did not all end held for review."""


def reset_status(engine: Engine) -> tuple[bool, int | None]:
    """Whether a reset is running, and the seconds since the last one finished."""
    with Session(engine) as session:
        row = session.execute(
            select(BetaFlag.value, func.extract("epoch", func.now() - BetaFlag.updated_at)).where(
                BetaFlag.name == RESETTING
            )
        ).one_or_none()
    if row is None:
        return False, None
    running, age = row
    return bool(running), None if running else math.floor(age)


def reset_record(engine: Engine) -> tuple[bool, datetime | None]:
    """Whether a reset is running or was cut off, and when the last one finished."""
    with Session(engine) as session:
        row = session.execute(
            select(BetaFlag.value, BetaFlag.updated_at).where(BetaFlag.name == RESETTING)
        ).one_or_none()
    if row is None:
        return False, None
    return row.value, None if row.value else row.updated_at


def latest_slot(now: datetime) -> datetime:
    """The most recent 08:00 UTC at or before now."""
    slot = now.astimezone(UTC).replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def due(running: bool, finished: datetime | None, now: datetime) -> bool:
    """A reset is due when none has finished since the latest 08:00 UTC, which also catches
    up a run missed while the host was down, or when the last one never finished."""
    return running or finished is None or finished < latest_slot(now)


def next_slot(now: datetime) -> datetime:
    return latest_slot(now) + timedelta(days=1)


def open_documents(engine: Engine) -> list[uuid.UUID]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(Document.id).where(
                    Document.status.not_in([status.value for status in TERMINAL_STATUSES])
                )
            )
        )


async def close_workflows(temporal: Client, documents: list[uuid.UUID]) -> None:
    """Terminate each document's workflow and wait until Temporal reports it closed."""
    handles = [temporal.get_workflow_handle(f"doc-{document}") for document in documents]
    for handle in handles:
        try:
            await handle.terminate(reason="Nightly beta reset")
        except RPCError as exc:
            # Already closed, or never started.
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
    for handle in handles:
        for _ in range(600):
            try:
                description = await handle.describe()
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                break
            if description.status != WorkflowExecutionStatus.RUNNING:
                break
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError(f"Workflow {handle.id} did not close")


def begin(engine: Engine, lock_timeout_ms: int) -> None:
    """Set the flag while holding the reset lock exclusively. That waits for any sample still
    writing its document, and every sample after it sees the flag."""
    with Session(engine) as session, session.begin():
        session.execute(text(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}"))
        session.execute(select(func.pg_advisory_xact_lock(RESET_LOCK)))
        session.execute(
            insert(BetaFlag)
            .values(name=RESETTING, value=True)
            .on_conflict_do_update(
                index_elements=[BetaFlag.name], set_={"value": True, "updated_at": func.now()}
            )
        )


def clear_tables(engine: Engine) -> None:
    """Remove every document and its rows in one statement, so a late write from a
    terminated workflow's activity lands either before it, and goes, or after it, and fails
    on the missing document. The spend ledger and the day's sample count stay, so a reset
    never lifts a daily cap."""
    with Session(engine) as session, session.begin():
        session.execute(text("TRUNCATE events, document_sources, filed_documents, documents"))


def prune_counters(engine: Engine) -> None:
    window_start = func.now() - COUNTER_WINDOW
    with Session(engine) as session, session.begin():
        session.execute(delete(SigninFailure).where(SigninFailure.at <= window_start))
        session.execute(delete(SampleSend).where(SampleSend.at <= window_start))


def empty_bucket(runtime: Runtime) -> int:
    bucket = runtime.settings.s3_bucket
    removed = 0
    while True:
        listed = runtime.s3.list_objects_v2(Bucket=bucket)
        keys = [item["Key"] for item in listed.get("Contents", [])]
        if not keys:
            return removed
        for key in keys:
            runtime.s3.delete_object(Bucket=bucket, Key=key)
        removed += len(keys)


async def reseed(runtime: Runtime, temporal: Client) -> list[str]:
    ingress = Ingress(runtime, temporal)
    ids = []
    for name in STARTING_QUEUE:
        content = await asyncio.to_thread((CORPUS / name).read_bytes)
        ids.append(
            await ingress.submit(
                content, name, source="sample", sender=SAMPLE_SENDER, model_mode="replay"
            )
        )
    return ids


async def settled(engine: Engine, ids: list[str], timeout: float) -> dict[str, int]:
    """Wait up to timeout seconds for the reseeded documents to leave processing, and count
    them by status."""
    keys = [uuid.UUID(value) for value in ids]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        with Session(engine) as session:
            statuses = list(session.scalars(select(Document.status).where(Document.id.in_(keys))))
        if loop.time() >= deadline or not {"received", "processing"} & set(statuses):
            return {status: statuses.count(status) for status in sorted(set(statuses))}
        await asyncio.sleep(0.2)


async def reset(
    runtime: Runtime, temporal: Client, settle_timeout: float = 120, lock_timeout_ms: int = 10_000
) -> dict:
    """Run the nightly reset once. If any step fails, the flag stays set, so samples keep
    getting a 503 and the next run starts again from the first step."""
    engine = runtime.engine
    try:
        await asyncio.to_thread(begin, engine, lock_timeout_ms)
    except Exception:
        # The lock was not taken, so the flag set with it was rolled back; set it anyway.
        set_flag(engine, RESETTING, True)
        raise
    documents = await asyncio.to_thread(open_documents, engine)
    await close_workflows(temporal, documents)
    await asyncio.to_thread(clear_tables, engine)
    removed = await asyncio.to_thread(empty_bucket, runtime)
    await asyncio.to_thread(prune_counters, engine)
    ids = await reseed(runtime, temporal)
    statuses = await settled(engine, ids, settle_timeout)
    if statuses != {"needs_review": len(STARTING_QUEUE)}:
        # The flag stays set, so the catch-up runs the whole reset again.
        raise IncompleteQueue(f"The starting queue did not settle as held: {statuses}")
    set_flag(engine, RESETTING, False)
    return {"terminated": len(documents), "objects_removed": removed, "seeded": statuses}


async def run_service(
    runtime: Runtime,
    temporal: Client,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Reset at 08:00 UTC every day, and at startup when a reset is due."""
    READY_FILE.touch()
    while True:
        if due(*await asyncio.to_thread(reset_record, runtime.engine), now()):
            try:
                print(f"Beta reset: {await reset(runtime, temporal)}", flush=True)
            except Exception:
                log.exception("Beta reset failed; trying again in %s seconds", RETRY_SECONDS)
                await sleep(RETRY_SECONDS)
                continue
        await sleep(max(1.0, (next_slot(now()) - now()).total_seconds()))
