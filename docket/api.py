import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.service import RPCError

from docket.core.ledger import TERMINAL_STATUSES
from docket.db import Document, Event
from docket.ingress import Ingress
from docket.runtime import Runtime


def create_app(runtime: Runtime | None = None, temporal: Client | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime or Runtime()
        app.state.runtime.ensure_bucket()
        app.state.temporal = temporal or await Client.connect(
            app.state.runtime.settings.temporal_address
        )
        app.state.ingress = Ingress(app.state.runtime, app.state.temporal)
        yield
        if runtime is None:
            app.state.runtime.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/documents")
    async def documents(
        file: Annotated[UploadFile, File()],
        message_id: str = Form(""),
        sender: str = Form(""),
        mode: str = Form("act"),
        run_id: str | None = Form(None),
    ):
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "Attachment exceeds 10 MB")
        try:
            document_id = await app.state.ingress.submit(
                content,
                file.filename or "attachment.pdf",
                message_id=message_id,
                sender=sender,
                mode=mode,
                run_id=run_id,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"document_id": document_id}

    @app.get("/documents/{document_id}")
    async def document_status(document_id: uuid.UUID):
        with Session(app.state.runtime.engine) as session:
            document = session.get(Document, document_id)
            if document is None:
                raise HTTPException(404, "Unknown document")
            events = session.scalars(
                select(Event).where(Event.document_id == document_id).order_by(Event.id)
            ).all()
            return {
                "document_id": str(document.id),
                "status": document.status,
                "reason": document.status_reason,
                "record_id": document.record_id,
                "category": document.category,
                "score": str(document.score),
                "events": [{"step": event.step, "detail": event.detail} for event in events],
            }

    @app.post("/review/{document_id}/{action}")
    async def review(document_id: uuid.UUID, action: str, body: dict[str, str]):
        with Session(app.state.runtime.engine) as session:
            document = session.get(Document, document_id)
            if document is None:
                raise HTTPException(404, "Unknown document")
            if document.status in TERMINAL_STATUSES:
                raise HTTPException(409, "Document is terminal")
        try:
            await app.state.temporal.get_workflow_handle(f"doc-{document_id}").execute_update(
                "review_action",
                {
                    "action": action,
                    "value": body.get("record_id" if action == "approve" else "reason", ""),
                },
            )
        except (WorkflowUpdateFailedError, RPCError) as exc:
            raise HTTPException(409, "Duplicate, late or invalid review action") from exc
        return {"accepted": True}

    return app


app = create_app()
