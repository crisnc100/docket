import base64
import binascii
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.service import RPCError

from docket.beta import (
    client_address,
    known_code,
    refuse_unsafe_start,
    release_attempt,
    reserve_attempt,
    retry_after,
    session_cookie,
    session_value,
)
from docket.core.ledger import TERMINAL_STATUSES
from docket.db import Document, DocumentSource, Event
from docket.ingress import Ingress
from docket.review_access import ReviewerAccess, reviewer_page
from docket.runtime import Runtime
from docket.webhook import BodyLimit, RateLimit, valid_signature


def no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL is not allowed in document metadata")
    return value


type MetadataText = Annotated[str, AfterValidator(no_nul)]


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: MetadataText = Field(min_length=1, max_length=255)
    content: str


class Inbound(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    message_id: MetadataText = Field(min_length=1, max_length=1000)
    sender: MetadataText = Field(alias="from", max_length=1000)
    attachments: list[Attachment] = Field(min_length=1, max_length=100)


class InviteCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=256)


BETA_MAX_REASON = 300


def encodes_as_utf8(value: str) -> bool:
    # JSON can carry a lone surrogate such as "\ud800"; Postgres would refuse it at finalize.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def document_candidates(session: Session, document_id: uuid.UUID) -> set[str]:
    """The record ids the review page offers for this document, from its latest score."""
    detail = session.scalar(
        select(Event.detail)
        .where(Event.document_id == document_id, Event.step == "score")
        .order_by(Event.id.desc())
    )
    return {item["record_id"] for item in (detail or {}).get("candidates", [])}


def create_app(runtime: Runtime | None = None, temporal: Client | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime or Runtime()
        refuse_unsafe_start(app.state.runtime.settings)
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
    app.add_middleware(ReviewerAccess, settings=lambda: app.state.runtime.settings)

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
            for value in (message_id, sender, file.filename or ""):
                no_nul(value)
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
        beta = app.state.runtime.settings.docket_env == "beta"
        with Session(app.state.runtime.engine) as session:
            document = session.get(Document, document_id)
            if document is None:
                raise HTTPException(404, "Unknown document")
            if document.status in TERMINAL_STATUSES:
                raise HTTPException(409, "Document is terminal")
            # Temporal keeps every update it receives, even one the workflow then rejects, so
            # requests that cannot succeed stop here.
            if action not in {"approve", "dismiss"}:
                raise HTTPException(409, "Unknown review action")
            reason = body.get("reason", "")
            if action == "dismiss" and "\x00" in reason:
                raise HTTPException(422, "NUL is not allowed in a dismiss reason")
            if action == "dismiss" and not encodes_as_utf8(reason):
                raise HTTPException(422, "A dismiss reason must be valid UTF-8 text")
            if beta and action == "dismiss" and not reason.strip():
                raise HTTPException(422, "A dismiss reason is required")
            if beta and action == "dismiss" and len(reason) > BETA_MAX_REASON:
                raise HTTPException(
                    422, f"A dismiss reason is at most {BETA_MAX_REASON} characters"
                )
            if beta and action == "approve":
                if body.get("record_id", "") not in document_candidates(session, document_id):
                    raise HTTPException(422, "The record is not one of this document's candidates")
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
    serve_home(app, static / "home")

    @app.get("/reviewer", include_in_schema=False)
    def review_page():
        beta = app.state.runtime.settings.docket_env == "beta"
        return reviewer_page(access="invite" if beta else "token")

    @app.post("/reviewer/session", include_in_schema=False)
    def reviewer_session(request: Request, body: InviteCode):
        settings = app.state.runtime.settings
        if settings.docket_env != "beta":
            raise HTTPException(404, "Not found")
        address = client_address(request.scope, settings)
        attempt = reserve_attempt(app.state.runtime.engine, address)
        if attempt is None:
            raise HTTPException(
                429,
                "Too many wrong codes from this address. Try again later.",
                headers={"Retry-After": str(retry_after(app.state.runtime.engine, address))},
            )
        if not known_code(settings, body.code):
            raise HTTPException(401, "That invite code is not valid.")
        release_attempt(app.state.runtime.engine, attempt)
        return Response(
            status_code=204,
            headers={
                "Set-Cookie": session_cookie(session_value(settings, body.code)),
                "Cache-Control": "no-store",
            },
        )

    return app


HOME_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


def serve_home(app: FastAPI, home: Path) -> None:
    """The public front page at "/" and its files under /home/. ReviewerAccess lets exactly
    these GETs through without a token or session; nothing here reads the database or the
    review queue."""
    root = home.resolve()

    @app.get("/", include_in_schema=False)
    def front_page():
        page = root / "index.html"
        if not page.is_file():
            return PlainTextResponse(
                "Build the front page with npm --prefix web run build", 503, headers=HOME_HEADERS
            )
        return FileResponse(page, headers=HOME_HEADERS)

    @app.get("/home/{path:path}", include_in_schema=False)
    def home_file(path: str):
        if path in {"", "index.html"}:
            return RedirectResponse("/", 308)
        # Path.resolve raises on a NUL byte; treat it like any other path that is not a file.
        if "\x00" in path:
            raise HTTPException(404, "Not found")
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(target, headers=HOME_HEADERS)


app = create_app()
