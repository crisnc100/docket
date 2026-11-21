"""The hosted beta's private sandboxes: where a sandbox's documents are kept, who may read
them, and the lock that keeps a write and "Delete now" apart."""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.orm import Session
from starlette.requests import cookie_parser

from docket.db import Document, Sandbox

SANDBOX_COOKIE = "__Host-docket_sandbox"
UNKNOWN_DOCUMENT = "Unknown document"


class SandboxClosed(Exception):
    """The sandbox was deleted, or expired, before the write could start."""


@dataclass(frozen=True)
class Owner:
    """Whose documents a request may see. Local mode has no sandboxes, so its documents have
    none; in beta, a request without a live sandbox sees nothing."""

    beta: bool
    sandbox_id: uuid.UUID | None


def raw_key(sandbox_id: uuid.UUID | None, digest: str) -> str:
    return f"sbx/{sandbox_id}/raw/{digest}" if sandbox_id else f"raw/{digest}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lock_key(sandbox_id: uuid.UUID) -> int:
    return int.from_bytes(hashlib.sha256(b"sandbox:" + sandbox_id.bytes).digest()[:8], signed=True)


def cookie_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    values = [value.decode("latin-1") for key, value in headers if key.lower() == b"cookie"]
    return cookie_parser("; ".join(values)).get(SANDBOX_COOKIE) or None


def live_sandbox(session: Session, token: str | None) -> Sandbox | None:
    """The cookie's sandbox while it is neither closed nor expired."""
    if token is None:
        return None
    return session.scalar(
        select(Sandbox).where(
            Sandbox.token_hash == token_hash(token),
            Sandbox.closed_at.is_(None),
            Sandbox.expires_at > func.now(),
        )
    )


def request_owner(engine: Engine, beta: bool, headers: list[tuple[bytes, bytes]]) -> Owner:
    if not beta:
        return Owner(False, None)
    with Session(engine) as session:
        sandbox = live_sandbox(session, cookie_token(headers))
        return Owner(True, sandbox.id if sandbox else None)


def owned_document(session: Session, owner: Owner, document_id: uuid.UUID) -> Document:
    """The document, only if it is in the owner's sandbox. Anything else gets the same 404
    as an id that does not exist, so a visitor cannot tell another's document from none."""
    if owner.beta and owner.sandbox_id is None:
        raise HTTPException(404, UNKNOWN_DOCUMENT)
    document = session.get(Document, document_id)
    if document is None or document.sandbox_id != owner.sandbox_id:
        raise HTTPException(404, UNKNOWN_DOCUMENT)
    return document


def _take_shared(engine: Engine, sandbox_id: uuid.UUID) -> Connection:
    connection = engine.connect()
    try:
        connection.begin()
        connection.execute(select(func.pg_advisory_xact_lock_shared(lock_key(sandbox_id))))
        writable = connection.execute(
            select(Sandbox.id).where(
                Sandbox.id == sandbox_id,
                Sandbox.closed_at.is_(None),
                Sandbox.expires_at > func.now(),
            )
        ).first()
    except BaseException:
        connection.close()
        raise
    if writable is None:
        connection.close()
        raise SandboxClosed(str(sandbox_id))
    return connection


@asynccontextmanager
async def writing_to(engine: Engine, sandbox_id: uuid.UUID) -> AsyncIterator[None]:
    """Hold the sandbox's lock shared for a whole write: the object, the rows and the
    workflow start. "Delete now" takes it exclusively, so it waits for a write already
    under way and deletes what it wrote, and a write that comes after finds the sandbox
    closed and writes nothing."""
    connection = await asyncio.to_thread(_take_shared, engine, sandbox_id)
    try:
        yield
    finally:
        # Closing the connection ends its transaction, which releases the lock.
        await asyncio.to_thread(connection.close)
