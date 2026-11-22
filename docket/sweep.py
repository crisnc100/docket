"""How a sandbox's documents leave every store: "Delete now" for one sandbox, and the sweep
for every sandbox that is no longer live.

The sweep runs three passes, one per store, each with its own time budget. Each pass decides
what to delete from what is live in Postgres, never from what another pass did, so a store
that is down stops only its own pass, and a pass cut short carries on at the next run."""

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from botocore.exceptions import ClientError
from sqlalchemy import Engine, and_, delete, exists, func, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import DeleteWorkflowExecutionRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from docket.db import BetaFlag, Document, SampleSend, Sandbox, SandboxCreation
from docket.sandbox import lock_key, token_hash

if TYPE_CHECKING:
    from docket.runtime import Runtime

SWEEP_INTERVAL = 120
PASS_BUDGET = 50
# A run that takes longer has hung somewhere; the process exits and compose restarts it.
RUN_LIMIT = 180
STALE_AFTER = 600
LOCK_TIMEOUT_MS = 10_000
BATCH = 1000
# The counters' rows older than this no longer count toward any limit.
COUNTER_WINDOW = timedelta(hours=1)
# The flag's updated_at is when the last run whose three passes all finished ended.
SWEPT = "sweep_complete"
CATCHING_UP = "Docket is catching up. Try again in a few minutes."
DELETING = "Deleting; this finishes within a few minutes."
READY_FILE = Path("/tmp/docket-sweep-ready")
TEMPORAL_TIMEOUT = timedelta(seconds=10)
# SeaweedFS frees a deleted object's bytes from a queue it works through about once a
# second (measured: 1.0 to 1.4 s after the delete), so a vacuum straight after the delete
# would miss them.
FILER_SETTLE = 3.0

log = logging.getLogger(__name__)

type Connect = Callable[[], Awaitable[Client]]


class Deadline:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.end = clock() + seconds

    def passed(self) -> bool:
        return self.clock() >= self.end


def sweep_age(engine: Engine) -> float | None:
    """Seconds since the last complete sweep, or None if none has completed."""
    with Session(engine) as session:
        age = session.scalar(
            select(func.extract("epoch", func.now() - BetaFlag.updated_at)).where(
                BetaFlag.name == SWEPT
            )
        )
    return None if age is None else float(age)


def sweep_stale(engine: Engine) -> bool:
    """True until a sweep has completed in the last ten minutes. Writes stop while it is,
    so data never piles up faster than it is deleted."""
    age = sweep_age(engine)
    return age is None or age > STALE_AFTER


def record_complete(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            insert(BetaFlag)
            .values(name=SWEPT, value=True)
            .on_conflict_do_update(
                index_elements=[BetaFlag.name], set_={"value": True, "updated_at": func.now()}
            )
        )


def _lock_timed_out(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == "55P03"


def _delete_locked(engine: Engine, sandbox_id: uuid.UUID) -> bool:
    """Delete the sandbox's row, and through it every row of its documents, once no write to
    it is under way. False if a write held it past the lock timeout."""
    with Session(engine) as session:
        try:
            with session.begin():
                session.execute(text(f"SET LOCAL lock_timeout = {int(LOCK_TIMEOUT_MS)}"))
                session.execute(select(func.pg_advisory_xact_lock(lock_key(sandbox_id))))
                session.execute(delete(Sandbox).where(Sandbox.id == sandbox_id))
        except OperationalError as exc:
            if not _lock_timed_out(exc):
                raise
            return False
    return True


def postgres_pass(engine: Engine, deadline: Deadline, everything: bool = False) -> tuple[bool, int]:
    """Delete the rows of every sandbox that is no longer live, or of every sandbox when
    everything is set, which also deletes the documents outside any sandbox. Returns
    whether it finished and how many sandboxes it deleted."""
    has_documents = exists().where(Document.sandbox_id == Sandbox.id)
    # An expired sandbox that was never closed and holds nothing has no write under way: a
    # write starts only on a sandbox with more than five minutes left. These go in bulk.
    idle = and_(Sandbox.expires_at <= func.now(), Sandbox.closed_at.is_(None), ~has_documents)
    gone = (
        true()
        if everything
        else or_(Sandbox.closed_at.is_not(None), Sandbox.expires_at <= func.now())
    )
    deleted, finished = 0, True
    skipped: set[uuid.UUID] = set()
    # Sandboxes with documents first, so a backlog of empty ones never holds up a document.
    while True:
        if deadline.passed():
            return False, deleted
        with Session(engine) as session:
            sandbox_id = session.scalar(
                select(Sandbox.id)
                .where(gone, ~idle, Sandbox.id.not_in(skipped) if skipped else true())
                .order_by(~has_documents, Sandbox.created_at)
                .limit(1)
            )
        if sandbox_id is None:
            break
        if _delete_locked(engine, sandbox_id):
            deleted += 1
        else:
            skipped.add(sandbox_id)
            finished = False
    while True:
        if deadline.passed():
            return False, deleted
        oldest = select(Sandbox.id).where(idle).order_by(Sandbox.created_at).limit(BATCH)
        with Session(engine) as session, session.begin():
            count = len(
                session.execute(
                    delete(Sandbox)
                    .where(Sandbox.id.in_(oldest.scalar_subquery()))
                    .returning(Sandbox.id)
                ).all()
            )
        deleted += count
        if count < BATCH:
            break
    if everything:
        while True:
            if deadline.passed():
                return False, deleted
            loose = select(Document.id).where(Document.sandbox_id.is_(None)).limit(BATCH)
            with Session(engine) as session, session.begin():
                count = len(
                    session.execute(
                        delete(Document)
                        .where(Document.id.in_(loose.scalar_subquery()))
                        .returning(Document.id)
                    ).all()
                )
            if count < BATCH:
                break
    window_start = func.now() - COUNTER_WINDOW
    with Session(engine) as session, session.begin():
        session.execute(delete(SandboxCreation).where(SandboxCreation.at <= window_start))
        session.execute(delete(SampleSend).where(SampleSend.at <= window_start))
    return finished, deleted


async def delete_workflow(temporal: Client, workflow_id: str, run_id: str = "") -> None:
    """Delete one workflow's history, terminating it first if it still runs."""
    try:
        await temporal.workflow_service.delete_workflow_execution(
            DeleteWorkflowExecutionRequest(
                namespace=temporal.namespace,
                workflow_execution=WorkflowExecution(workflow_id=workflow_id, run_id=run_id),
            ),
            timeout=TEMPORAL_TIMEOUT,
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise


def _document_id(workflow_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(workflow_id.removeprefix("doc-"))
    except ValueError:
        return None


def _present(engine: Engine, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    with Session(engine) as session:
        return set(session.scalars(select(Document.id).where(Document.id.in_(ids))))


async def _delete_orphans(engine: Engine, temporal: Client, page: list[tuple[str, str]]) -> int:
    ids = {workflow_id: _document_id(workflow_id) for workflow_id, _ in page}
    present = await asyncio.to_thread(
        _present, engine, [value for value in ids.values() if value is not None]
    )
    deleted = 0
    for workflow_id, run_id in page:
        document_id = ids[workflow_id]
        # Not one of Docket's document ids, so not Docket's to delete.
        if document_id is None or document_id in present:
            continue
        await delete_workflow(temporal, workflow_id, run_id)
        deleted += 1
    return deleted


async def temporal_pass(
    engine: Engine, temporal: Client, queue: str, deadline: Deadline, page_size: int = 500
) -> tuple[bool, int]:
    """Delete every document workflow on Docket's task queue whose document row is gone."""
    query = f"WorkflowId STARTS_WITH 'doc-' AND TaskQueue = '{queue}'"
    deleted = 0
    page: list[tuple[str, str]] = []
    async for execution in temporal.list_workflows(
        query, page_size=page_size, rpc_timeout=TEMPORAL_TIMEOUT
    ):
        page.append((execution.id, execution.run_id))
        if len(page) >= page_size:
            deleted += await _delete_orphans(engine, temporal, page)
            page = []
        if deadline.passed():
            return False, deleted
    deleted += await _delete_orphans(engine, temporal, page)
    return True, deleted


def delete_prefix(
    runtime: "Runtime", prefix: str, deadline: Deadline | None = None
) -> tuple[bool, int]:
    """Delete every object under the prefix. Returns whether it finished before the
    deadline, and how many objects it deleted."""
    bucket = runtime.settings.s3_bucket
    deleted = 0
    while True:
        listed = runtime.s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=BATCH)
        keys = [item["Key"] for item in listed.get("Contents", [])]
        if not keys:
            return True, deleted
        for key in keys:
            if deadline is not None and deadline.passed():
                return False, deleted
            runtime.s3.delete_object(Bucket=bucket, Key=key)
            deleted += 1


def _live(engine: Engine, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    with Session(engine) as session:
        return set(
            session.scalars(
                select(Sandbox.id).where(
                    Sandbox.id.in_(ids),
                    Sandbox.closed_at.is_(None),
                    Sandbox.expires_at > func.now(),
                )
            )
        )


def vacuum(master_url: str) -> None:
    """Have SeaweedFS rewrite its volumes without the deleted objects. Until then their bytes
    stay in the volume files."""
    response = httpx.get(
        master_url.rstrip("/") + "/vol/vacuum",
        params={"garbageThreshold": "0.0001"},
        timeout=30,
    )
    response.raise_for_status()


def storage_pass(
    runtime: "Runtime", deadline: Deadline, everything: bool = False
) -> tuple[bool, int]:
    """Delete the objects of every sandbox that is not live, or every object when everything
    is set, then vacuum. Returns whether it finished and how many sandboxes' objects it
    deleted."""
    bucket = runtime.settings.s3_bucket
    deleted, objects, finished = 0, 0, True
    token = None
    try:
        while True:
            kwargs = {"ContinuationToken": token} if token else {}
            listed = runtime.s3.list_objects_v2(
                Bucket=bucket, Prefix="sbx/", Delimiter="/", MaxKeys=BATCH, **kwargs
            )
            prefixes = [item["Prefix"] for item in listed.get("CommonPrefixes", [])]
            ids = {}
            for prefix in prefixes:
                try:
                    ids[prefix] = uuid.UUID(prefix.removeprefix("sbx/").removesuffix("/"))
                except ValueError:
                    ids[prefix] = None
            live = set() if everything else _live(runtime.engine, [v for v in ids.values() if v])
            for prefix, sandbox_id in ids.items():
                if sandbox_id in live:
                    continue
                finished, count = delete_prefix(runtime, prefix, deadline)
                objects += count
                if not finished:
                    break
                deleted += 1
            if not finished or not listed.get("IsTruncated"):
                break
            token = listed["NextContinuationToken"]
        if finished and everything:
            finished, count = delete_prefix(runtime, "", deadline)
            objects += count
    except ClientError as exc:
        # The API makes the bucket when it starts; before that, nothing is stored.
        if exc.response["Error"]["Code"] != "NoSuchBucket":
            raise
    # Even after a pass cut short, so what it did delete leaves the disk now. A pass that
    # deleted nothing still vacuums, for bytes an earlier vacuum came too soon for.
    if objects:
        time.sleep(FILER_SETTLE)
    vacuum(runtime.settings.seaweedfs_master_url)
    return finished, deleted


async def _run_pass(name: str, report: dict, work: Awaitable[tuple[bool, int]]) -> None:
    try:
        finished, count = await work
    except Exception:
        log.warning("The sweep's %s pass failed", name, exc_info=True)
        report[name] = "failed"
        return
    report[name] = "complete" if finished else "cut short"
    report[name + "_deleted"] = count


async def sweep_once(
    runtime: "Runtime",
    connect: Connect,
    *,
    everything: bool = False,
    budget: float = PASS_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """One run of the three passes. Records the sweep as complete only when all three
    finished."""
    engine = runtime.engine
    report: dict = {}
    await _run_pass(
        "postgres",
        report,
        asyncio.to_thread(postgres_pass, engine, Deadline(budget, clock), everything),
    )

    async def temporal_work() -> tuple[bool, int]:
        deadline = Deadline(budget, clock)
        temporal = await connect()
        return await asyncio.wait_for(
            temporal_pass(engine, temporal, runtime.settings.docket_task_queue, deadline),
            budget + TEMPORAL_TIMEOUT.total_seconds(),
        )

    await _run_pass("temporal", report, temporal_work())
    await _run_pass(
        "storage",
        report,
        asyncio.to_thread(storage_pass, runtime, Deadline(budget, clock), everything),
    )
    report["complete"] = all(
        report[name] == "complete" for name in ("postgres", "temporal", "storage")
    )
    if report["complete"]:
        await asyncio.to_thread(record_complete, engine)
    return report


def temporal_connector(address: str) -> Connect:
    """Connect on first use and keep the client, so Temporal being down at startup stops
    only the Temporal pass, and each run tries again."""
    client: Client | None = None

    async def connect() -> Client:
        nonlocal client
        if client is None:
            client = await asyncio.wait_for(
                Client.connect(address), TEMPORAL_TIMEOUT.total_seconds()
            )
        return client

    return connect


async def run_service(
    runtime: "Runtime",
    connect: Connect,
    *,
    interval: float = SWEEP_INTERVAL,
    limit: float = RUN_LIMIT,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    leave: Callable[[int], None] = os._exit,
) -> None:
    """Sweep every interval seconds, start to start."""
    READY_FILE.touch()
    while True:
        started = clock()
        try:
            report = await asyncio.wait_for(sweep_once(runtime, connect), limit)
        except TimeoutError:
            print(f"Sweep run passed {limit:.0f} seconds; exiting to restart.", flush=True)
            # A pass stuck in a thread cannot be cancelled, so the process goes.
            leave(1)
            return
        except Exception:
            log.exception("Sweep run failed")
        else:
            print(f"Sweep: {report}", flush=True)
        await sleep(max(0.0, interval - (clock() - started)))


def _close(engine: Engine, token: str) -> tuple[uuid.UUID, list[uuid.UUID], bool] | None:
    """Delete the cookie's sandbox's rows under its lock, and return its id and documents.
    When a write holds the lock past the timeout, close the sandbox instead, so no later
    write starts and the sweep deletes it."""
    with Session(engine) as session:
        sandbox_id = session.scalar(
            select(Sandbox.id).where(Sandbox.token_hash == token_hash(token))
        )
        session.rollback()
        if sandbox_id is None:
            return None
        try:
            with session.begin():
                session.execute(text(f"SET LOCAL lock_timeout = {int(LOCK_TIMEOUT_MS)}"))
                session.execute(select(func.pg_advisory_xact_lock(lock_key(sandbox_id))))
                documents = list(
                    session.scalars(select(Document.id).where(Document.sandbox_id == sandbox_id))
                )
                session.execute(delete(Sandbox).where(Sandbox.id == sandbox_id))
        except OperationalError as exc:
            if not _lock_timed_out(exc):
                raise
            with session.begin():
                session.execute(
                    update(Sandbox)
                    .where(Sandbox.id == sandbox_id)
                    .values(closed_at=func.coalesce(Sandbox.closed_at, func.now()))
                )
            return sandbox_id, [], False
    return sandbox_id, documents, True


async def delete_now(runtime: "Runtime", temporal: Client, token: str | None) -> bool:
    """Delete the cookie's sandbox from all three stores, then vacuum. Returns False when a
    step was left to the sweep: the rows are gone or the sandbox is closed either way, so
    nothing new is written to it."""
    if token is None:
        return True
    closed = await asyncio.to_thread(_close, runtime.engine, token)
    if closed is None:
        return True
    sandbox_id, documents, complete = closed
    if not complete:
        return False
    try:
        for document_id in documents:
            await delete_workflow(temporal, f"doc-{document_id}")
    except Exception:
        log.warning("Delete now left workflows to the sweep", exc_info=True)
        complete = False
    try:
        _, count = await asyncio.to_thread(delete_prefix, runtime, f"sbx/{sandbox_id}/")
        if count:
            await asyncio.sleep(FILER_SETTLE)
        await asyncio.to_thread(vacuum, runtime.settings.seaweedfs_master_url)
    except Exception:
        log.warning("Delete now left objects to the sweep", exc_info=True)
        complete = False
    return complete
