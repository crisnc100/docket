import base64
import binascii
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.service import RPCError

from docket.core.ledger import TERMINAL_STATUSES
from docket.db import Document, DocumentSource, Event
from docket.ingress import Ingress
from docket.runtime import Runtime
from docket.webhook import BodyLimit, RateLimit, valid_signature


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content: str


class Inbound(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    message_id: str = Field(min_length=1, max_length=1000)
    sender: str = Field(alias="from", max_length=1000)
    attachments: list[Attachment] = Field(min_length=1, max_length=100)


def create_app(runtime: Runtime | None = None, temporal: Client | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime or Runtime()
        app.state.runtime.ensure_bucket()
        app.state.temporal = temporal or await Client.connect(
            app.state.runtime.settings.temporal_address
        )
        app.state.rate_limit = RateLimit(app.state.runtime.settings.docket_webhook_rate_limit)
        app.state.ingress = Ingress(app.state.runtime, app.state.temporal)
        yield
        if runtime is None:
            app.state.runtime.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(BodyLimit)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/inbound")
    async def inbound(request: Request):
        ip = request.client.host if request.client else "unknown"
        if not app.state.rate_limit.allow(ip):
            raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
        body = await request.body()
        if not valid_signature(
            app.state.runtime.settings.docket_webhook_secret.get_secret_value(),
            request.headers.get("x-docket-timestamp", ""),
            request.headers.get("x-docket-signature", ""),
            body,
        ):
            raise HTTPException(401, "Invalid or stale webhook signature")
        try:
            payload = Inbound.model_validate_json(body)
            attachments = [
                base64.b64decode(item.content, validate=True) for item in payload.attachments
            ]
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(422, "Invalid webhook payload or base64 attachment") from exc
        ids = []
        for attachment, content in zip(payload.attachments, attachments, strict=True):
            ids.append(
                await app.state.ingress.submit(
                    content,
                    attachment.filename,
                    message_id=payload.message_id,
                    sender=payload.sender,
                    source="webhook",
                )
            )
        return {"document_ids": ids}

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
    def document_status(document_id: uuid.UUID):
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
                "score": str(document.score) if document.score is not None else None,
                "events": [{"step": event.step, "detail": event.detail} for event in events],
            }

    @app.get("/documents/{document_id}/file")
    def document_file(document_id: uuid.UUID):
        with Session(app.state.runtime.engine) as session:
            document = session.get(Document, document_id)
            if document is None:
                raise HTTPException(404, "Unknown document")
            if document.status == "rejected":
                raise HTTPException(415, "Rejected documents cannot be previewed")
            content_hash = document.content_hash
        content = app.state.runtime.read(f"raw/{content_hash}")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(415, "Document is not a PDF")
        return Response(
            content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'",
            },
        )

    @app.get("/review")
    def review_queue():
        with Session(app.state.runtime.engine) as session:
            documents = session.scalars(
                select(Document)
                .where(
                    Document.status == "needs_review",
                    Document.mode == "act",
                )
                .order_by(Document.created_at, Document.id)
            ).all()
            items = []
            for document in documents:
                events = {
                    event.step: event.detail
                    for event in session.scalars(
                        select(Event).where(Event.document_id == document.id).order_by(Event.id)
                    )
                }
                source = session.scalar(
                    select(DocumentSource)
                    .where(DocumentSource.document_id == document.id)
                    .order_by(DocumentSource.id)
                )
                scored = events.get("score", {})
                items.append(
                    {
                        "document_id": str(document.id),
                        "filename": events.get("received", {})
                        .get("workflow_input", {})
                        .get("filename", ""),
                        "sender": source.sender if source else "",
                        "created_at": document.created_at.isoformat(),
                        "reason": document.status_reason,
                        "score": str(document.score) if document.score is not None else None,
                        "tier": scored.get("tier"),
                        "candidates": scored.get("candidates", []),
                        "rules_top_record_id": scored.get("rules_top_record_id"),
                        "suggested_record_id": scored.get(
                            "suggested_record_id", events.get("choose", {}).get("record_id")
                        ),
                    }
                )
            return {"items": items}

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

    static = Path(__file__).resolve().parent.parent / "web" / "dist"
    app.mount("/assets", StaticFiles(directory=static / "assets", check_dir=False), name="assets")

    @app.get("/", include_in_schema=False)
    def review_page():
        if not (static / "index.html").is_file():
            raise HTTPException(503, "Build the review page with npm --prefix web run build")
        return FileResponse(
            static / "index.html",
            headers={
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app


app = create_app()
