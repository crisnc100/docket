import asyncio
import base64
import binascii
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from botocore.exceptions import ClientError
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, RootModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.service import RPCError

from docket.beta import address_hash, client_address, refuse_unsafe_start
from docket.beta_samples import (
    PAUSED,
    SAMPLE_LABELS,
    SAMPLE_SENDER,
    TURNED_OFF,
    SampleGate,
    take_slot,
)
from docket.core.ledger import TERMINAL_STATUSES
from docket.corpus import SAMPLE_CASE_TYPES, sample_document
from docket.db import Document, DocumentSource, Event
from docket.ingress import Ingress
from docket.review_access import ReviewerAccess, reviewer_page
from docket.runtime import Runtime
from docket.sandbox import (
    SANDBOX_ENDED,
    SANDBOX_ENDING,
    TOO_MANY_SANDBOXES,
    Owner,
    SandboxClosed,
    SandboxEnding,
    TooManySandboxes,
    close_sandbox,
    cookie_token,
    owned_document,
    raw_key,
    request_owner,
    sandbox_cookie,
    seconds_left,
    spent_cookie,
    write_target,
)
from docket.sweep import CATCHING_UP, DELETING, delete_now, sweep_stale
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


def encodes_as_utf8(value: str) -> bool:
    # JSON can carry a lone surrogate such as "\ud800", which Postgres and hashing refuse.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class ReviewBody(RootModel[dict[str, str]]):
    pass


class SampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_type: str


BETA_MAX_REASON = 300
REFUSALS = {"off": TURNED_OFF, "paused": PAUSED, "catching_up": CATCHING_UP}


async def read_json[T: BaseModel](request: Request, model: type[T], message: str) -> T:
    """Parse the raw body here, with one fixed message for every bad body. FastAPI's own 422
    echoes the input, and an input holding a lone surrogate such as "\\ud800" cannot be
    written into that JSON reply."""
    try:
        return model.model_validate(json.loads(await request.body()))
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, message) from exc


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
        settings = app.state.runtime.settings
        beta = settings.docket_env == "beta"
        app.state.sample_gate = SampleGate(app.state.runtime.engine, settings) if beta else None
        yield
        if runtime is None:
            app.state.runtime.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.add_middleware(
        ReviewerAccess, settings=lambda: app.state.runtime.settings, routes=lambda: app.routes
    )

    def owner(request: Request) -> Owner:
        beta = app.state.runtime.settings.docket_env == "beta"
        return request_owner(app.state.runtime.engine, beta, request.scope["headers"])

    @app.get("/healthz")
    async def health():
        """Public, so it says only whether the sweep is keeping up; `docket beta-status`
        shows the rest to the operator."""
        if app.state.runtime.settings.docket_env != "beta":
            return {"status": "ok"}
        stale = await asyncio.to_thread(sweep_stale, app.state.runtime.engine)
        return {"status": "stale" if stale else "ok"}

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
    def document_status(document_id: uuid.UUID, request: Request):
        visitor = owner(request)
        with Session(app.state.runtime.engine) as session:
            document = owned_document(session, visitor, document_id)
            events = session.scalars(
                select(Event).where(Event.document_id == document_id).order_by(Event.id)
            ).all()
            received = next((event.detail for event in events if event.step == "received"), {})
            source = session.scalar(
                select(DocumentSource)
                .where(DocumentSource.document_id == document_id)
                .order_by(DocumentSource.id)
            )
            return {
                "document_id": str(document.id),
                "filename": received.get("workflow_input", {}).get("filename", ""),
                "sender": source.sender if source else "",
                "status": document.status,
                "reason": document.status_reason,
                "record_id": document.record_id,
                "category": document.category,
                "score": str(document.score) if document.score is not None else None,
                "events": [{"step": event.step, "detail": event.detail} for event in events],
            }

    @app.get("/documents/{document_id}/file")
    def document_file(document_id: uuid.UUID, request: Request):
        visitor = owner(request)
        with Session(app.state.runtime.engine) as session:
            document = owned_document(session, visitor, document_id)
            if document.status == "rejected":
                raise HTTPException(415, "Rejected documents cannot be previewed")
            key = raw_key(document.sandbox_id, document.content_hash)
        try:
            content = app.state.runtime.read(key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"NoSuchKey", "404"}:
                raise
            # Delete now or the sweep took the file after the lookup: the document is gone
            # too, so it gets the same 404. A file missing from a document still there is a
            # storage fault and stays an error.
            with Session(app.state.runtime.engine) as session:
                owned_document(session, owner(request), document_id)
            raise
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
    def review_queue(request: Request):
        visitor = owner(request)
        if visitor.beta and visitor.sandbox_id is None:
            return {"items": []}
        with Session(app.state.runtime.engine) as session:
            documents = session.scalars(
                select(Document)
                .where(
                    Document.status == "needs_review",
                    Document.mode == "act",
                    Document.sandbox_id.is_not_distinct_from(visitor.sandbox_id),
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
    async def review(document_id: uuid.UUID, action: str, request: Request):
        visitor = await asyncio.to_thread(owner, request)

        def check_owner():
            with Session(app.state.runtime.engine) as session:
                owned_document(session, visitor, document_id)

        await asyncio.to_thread(check_owner)
        beta = app.state.runtime.settings.docket_env == "beta"
        # A dismiss reason is new text in the sandbox, so it waits for a recent sweep, as a
        # sample does.
        if beta and await asyncio.to_thread(sweep_stale, app.state.runtime.engine):
            raise HTTPException(503, CATCHING_UP)
        body = (await read_json(request, ReviewBody, "Send the review decision as JSON text.")).root
        with Session(app.state.runtime.engine) as session:
            document = owned_document(session, visitor, document_id)
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

    def sample_gate() -> SampleGate:
        if app.state.sample_gate is None:
            raise HTTPException(404, "Not found")
        return app.state.sample_gate

    @app.get("/beta/samples", include_in_schema=False)
    async def sample_options():
        gate = sample_gate()
        return {
            "state": await asyncio.to_thread(gate.state),
            "case_types": [
                {"value": value, "label": SAMPLE_LABELS[value]} for value in SAMPLE_CASE_TYPES
            ],
        }

    @app.post("/beta/samples", include_in_schema=False)
    async def send_sample(request: Request):
        gate = sample_gate()
        body = await read_json(request, SampleRequest, "Send one case type from the list.")
        if body.case_type not in SAMPLE_CASE_TYPES:
            raise HTTPException(422, "Send one case type from the list.")
        state = await asyncio.to_thread(gate.state)
        if state != "ready":
            raise HTTPException(503, REFUSALS[state])
        hashed = address_hash(gate.settings, client_address(request.scope, gate.settings))
        token = cookie_token(request.scope["headers"])

        def reserve():
            """The sandbox and the sample slot, together or not at all."""
            with Session(gate.engine) as session:
                target = write_target(session, token, hashed)
                refused = take_slot(session, hashed)
                if refused is None:
                    session.commit()
                else:
                    session.rollback()
                return target, refused

        def ended():
            """The cookie names a sandbox that ended, so the browser drops it and its next
            send starts a new one."""
            return JSONResponse(
                {"detail": SANDBOX_ENDED, "code": "sandbox_ended"},
                409,
                headers={"Set-Cookie": spent_cookie()},
            )

        try:
            target, refused = await asyncio.to_thread(reserve)
        except TooManySandboxes as exc:
            raise HTTPException(429, TOO_MANY_SANDBOXES) from exc
        except SandboxClosed:
            return ended()
        except SandboxEnding:
            return JSONResponse({"detail": SANDBOX_ENDING, "code": "sandbox_ending"}, 409)
        if refused is not None:
            text, wait = refused
            raise HTTPException(429, text, headers={"Retry-After": str(wait)})
        # A fresh seed from the server for every sample: a visitor never sends one.
        sample = await asyncio.to_thread(sample_document, body.case_type, secrets.randbits(63))
        try:
            document_id = await app.state.ingress.submit(
                sample.content,
                sample.filename,
                source="sample",
                sender=SAMPLE_SENDER,
                sandbox=target.sandbox_id,
            )
        except SandboxClosed:
            return ended()
        except BaseException:
            # The browser never gets the cookie of a sandbox this request made, so whatever
            # the failed write left in it goes to the sweep. Its slots stay taken.
            if target.token is not None:
                await asyncio.to_thread(close_sandbox, gate.engine, target.sandbox_id)
            raise
        headers = {}
        if target.token is not None:
            left = await asyncio.to_thread(seconds_left, gate.engine, target.token)
            headers["Set-Cookie"] = sandbox_cookie(target.token, left or 0)
        return JSONResponse({"document_id": document_id}, headers=headers)

    def require_beta() -> None:
        if app.state.runtime.settings.docket_env != "beta":
            raise HTTPException(404, "Not found")

    @app.get("/sandbox", include_in_schema=False)
    def sandbox_status(request: Request):
        """The seconds the visitor's sandbox has left, for the page's countdown."""
        require_beta()
        token = cookie_token(request.scope["headers"])
        return {"expires_in": seconds_left(app.state.runtime.engine, token)}

    @app.delete("/sandbox", include_in_schema=False)
    async def delete_sandbox(request: Request):
        """Delete now. It answers the same with no cookie or an unknown one, so it reveals
        nothing, and it works even while the sweep is behind."""
        require_beta()
        token = cookie_token(request.scope["headers"])
        complete = await delete_now(app.state.runtime, app.state.temporal, token)
        headers = {"Set-Cookie": spent_cookie()}
        if complete:
            return JSONResponse({"deleted": True}, headers=headers)
        return JSONResponse({"deleted": False, "detail": DELETING}, 202, headers=headers)

    static = Path(__file__).resolve().parent.parent / "web" / "dist"
    app.mount("/assets", StaticFiles(directory=static / "assets", check_dir=False), name="assets")
    serve_home(app, static / "home")

    @app.get("/reviewer", include_in_schema=False)
    def review_page():
        beta = app.state.runtime.settings.docket_env == "beta"
        return reviewer_page(access="sandbox" if beta else "token")

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
