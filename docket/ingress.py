import asyncio
import contextlib
import hashlib
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from docket.core.ledger import TERMINAL_STATUSES, append_event, transition
from docket.db import Document, DocumentSource, Event
from docket.failpoints import fire
from docket.runtime import Runtime
from docket.sandbox import SandboxClosed, raw_key, writing_to
from docket.workflows import DocumentInput, FileDocumentWorkflow


class Ingress:
    def __init__(self, runtime: Runtime, temporal: Client):
        self.runtime, self.temporal = runtime, temporal

    async def start(self, request: DocumentInput) -> None:
        try:
            await self.temporal.start_workflow(
                FileDocumentWorkflow.run,
                request,
                id=f"doc-{request.document_id}",
                task_queue=self.runtime.settings.docket_task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            pass

    async def submit(
        self,
        content: bytes,
        filename: str,
        *,
        mode: str = "act",
        run_id: str | None = None,
        message_id: str = "",
        sender: str = "",
        source: str = "http",
        model_mode: str | None = None,
        budget: str | None = None,
        review_timeout_seconds: float | None = None,
        sandbox: uuid.UUID | None = None,
    ) -> str:
        """A write to a sandbox raises SandboxClosed, having written nothing, when the
        sandbox is closed or expired by the time it starts."""
        if mode not in {"act", "observe"}:
            raise ValueError("Invalid mode")
        if mode == "act":
            if run_id not in {None, "live"}:
                raise ValueError("Act mode requires run_id live")
            run_id = "live"
        elif not run_id or run_id == "live":
            raise ValueError("Observe mode requires a fresh run id")
        digest = hashlib.sha256(content).hexdigest()
        key = raw_key(sandbox, digest)
        held = writing_to(self.runtime.engine, sandbox) if sandbox else contextlib.nullcontext()
        async with held as connection:
            await asyncio.to_thread(
                self.runtime.storage.put_if_absent, key, content, digest, original_filename=filename
            )
            fire("ingress.after_raw")

            def persist():
                bind = connection if connection is not None else self.runtime.engine
                with Session(bind) as session, session.begin():
                    new_id = uuid.uuid4()
                    created = session.execute(
                        insert(Document)
                        .values(
                            id=new_id,
                            sandbox_id=sandbox,
                            mode=mode,
                            run_id=run_id,
                            content_hash=digest,
                            status="received",
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                Document.sandbox_id,
                                Document.mode,
                                Document.run_id,
                                Document.content_hash,
                            ]
                        )
                        .returning(Document.id)
                    ).scalar_one_or_none()
                    document = session.scalar(
                        select(Document).where(
                            Document.sandbox_id.is_not_distinct_from(sandbox),
                            Document.mode == mode,
                            Document.run_id == run_id,
                            Document.content_hash == digest,
                        )
                    )
                    assert document is not None
                    # Two deliveries of the same message can arrive at once. The lock makes the
                    # second one wait, see the first source row and add none.
                    if source == "webhook":
                        lock = int.from_bytes(
                            hashlib.sha256(f"{document.id}:{message_id}".encode()).digest()[:8],
                            signed=True,
                        )
                        session.execute(select(func.pg_advisory_xact_lock(lock)))
                    seen = (
                        source == "webhook"
                        and session.scalar(
                            select(DocumentSource.id).where(
                                DocumentSource.document_id == document.id,
                                DocumentSource.source == source,
                                DocumentSource.message_id == message_id,
                            )
                        )
                        is not None
                    )
                    if not seen:
                        session.add(
                            DocumentSource(
                                document_id=document.id,
                                source=source,
                                message_id=message_id,
                                sender=sender,
                            )
                        )
                    if created:
                        request = DocumentInput(
                            str(document.id),
                            key,
                            filename,
                            mode,
                            run_id,
                            source,
                            model_mode or self.runtime.settings.docket_model_mode,
                            self.runtime.settings.docket_model,
                            budget or str(self.runtime.settings.docket_run_budget_usd),
                            review_timeout_seconds
                            or self.runtime.settings.docket_review_timeout_seconds,
                        )
                        append_event(
                            session, document.id, "received", {"workflow_input": asdict(request)}
                        )
                    else:
                        event = session.scalar(
                            select(Event).where(Event.idem_key == f"{document.id}:received")
                        )
                        assert event is not None
                        request = DocumentInput(**event.detail["workflow_input"])
                    return request, document.status

            request, status = await asyncio.to_thread(persist)
            fire("ingress.after_insert")
            if status not in TERMINAL_STATUSES:
                await self.start(request)
            return request.document_id

    async def reconcile(self, older_than_minutes: float = 5) -> dict[str, int]:
        cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
        with Session(self.runtime.engine) as session:
            rows = session.scalars(
                select(Document).where(
                    Document.status.not_in([value.value for value in TERMINAL_STATUSES]),
                    Document.updated_at <= cutoff,
                )
            ).all()
            pending = [
                (str(document.id), document.status, document.sandbox_id) for document in rows
            ]
        counts = {"started": 0, "orphaned": 0, "running": 0}
        for document_id, status, sandbox in pending:
            handle = self.temporal.get_workflow_handle(f"doc-{document_id}")
            try:
                description = await handle.describe()
                running = description.status == WorkflowExecutionStatus.RUNNING
                exists = True
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                running, exists = False, False
            if running:
                counts["running"] += 1
            elif status == "received" and not exists:
                try:
                    if await self._restart(document_id, sandbox):
                        counts["started"] += 1
                        continue
                except SandboxClosed:
                    # Its sandbox is being deleted; the sweep takes the document with it.
                    continue
            if not running:
                with Session(self.runtime.engine) as session, session.begin():
                    document = session.get(
                        Document,
                        uuid.UUID(document_id),
                        with_for_update=True,
                        populate_existing=True,
                    )
                    # None when its sandbox was deleted after the list was read.
                    if document is not None and document.status not in TERMINAL_STATUSES:
                        transition(session, document.id, "failed", "orphaned")
                        counts["orphaned"] += 1
        return counts

    async def _restart(self, document_id: str, sandbox: uuid.UUID | None) -> bool:
        """Start the workflow a received document never got. A sandbox's document is
        started under the sandbox's lock, as a new write is, so it never starts after Delete
        now has taken the sandbox; a closed or expired sandbox raises SandboxClosed."""
        held = writing_to(self.runtime.engine, sandbox) if sandbox else contextlib.nullcontext()
        async with held as connection:
            bind = connection if connection is not None else self.runtime.engine
            with Session(bind) as session:
                event = session.scalar(
                    select(Event).where(Event.idem_key == f"{document_id}:received")
                )
                request = DocumentInput(**event.detail["workflow_input"]) if event else None
            if request is None:
                return False
            await self.start(request)
            return True
