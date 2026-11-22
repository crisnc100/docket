"""The hosted beta's private sandboxes: where a sandbox's documents are kept, who may read
them, and the lock that keeps a write and "Delete now" apart."""

import asyncio
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import Connection, Engine, delete, func, insert, select, update
from sqlalchemy.orm import Session
from starlette.requests import cookie_parser

from docket.db import Document, Sandbox, SandboxCreation

SANDBOX_COOKIE = "__Host-docket_sandbox"
UNKNOWN_DOCUMENT = "Unknown document"
SANDBOX_LIFE = timedelta(minutes=50)
# In its last minutes a sandbox takes no new document: the next one starts a fresh sandbox,
# so nothing a visitor sends is deleted moments later.
ROLLOVER = timedelta(minutes=5)
CREATION_LIMIT = 10
CREATION_WINDOW = timedelta(hours=1)
TOO_MANY_SANDBOXES = "Too many new sandboxes from your network. Try again later."
SANDBOX_DELETED = "This sandbox was just deleted. Send again to start a new one."


class SandboxClosed(Exception):
    """The sandbox was deleted, or expired, before the write could start."""


class TooManySandboxes(Exception):
    """The address has started its hourly limit of new sandboxes."""


@dataclass(frozen=True)
class WriteTarget:
    """The sandbox a write goes to. token is set only for a sandbox this request created,
    whose cookie the response must set."""

    sandbox_id: uuid.UUID
    token: str | None


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


def writable_sandbox(session: Session, token: str | None) -> Sandbox | None:
    """The cookie's sandbox while it still takes documents: open, and not in its last
    minutes."""
    if token is None:
        return None
    return session.scalar(
        select(Sandbox).where(
            Sandbox.token_hash == token_hash(token),
            Sandbox.closed_at.is_(None),
            Sandbox.expires_at > func.now() + ROLLOVER,
        )
    )


def _creation_lock(address_hash: str) -> int:
    return int.from_bytes(
        hashlib.sha256(b"sandbox-creation:" + address_hash.encode()).digest()[:8], signed=True
    )


def write_target(session: Session, token: str | None, address_hash: str) -> WriteTarget:
    """The sandbox a write from this request goes to, inside the caller's transaction: the
    cookie's sandbox while it takes documents, or else a new one. Raises TooManySandboxes
    when the address has started its limit in the last hour; the count is taken under a lock
    for the address, so parallel requests never pass it."""
    current = writable_sandbox(session, token)
    if current is not None:
        return WriteTarget(current.id, None)
    window_start = func.now() - CREATION_WINDOW
    session.execute(select(func.pg_advisory_xact_lock(_creation_lock(address_hash))))
    session.execute(
        delete(SandboxCreation).where(
            SandboxCreation.address_hash == address_hash, SandboxCreation.at <= window_start
        )
    )
    recent = session.scalar(
        select(func.count()).where(SandboxCreation.address_hash == address_hash)
    )
    if recent is not None and recent >= CREATION_LIMIT:
        raise TooManySandboxes
    session.execute(insert(SandboxCreation).values(address_hash=address_hash))
    # 128 random bits. Only the hash is stored, so the database cannot hand a cookie back.
    new_token = secrets.token_urlsafe(16)
    sandbox_id = session.execute(
        insert(Sandbox)
        .values(
            id=uuid.uuid4(),
            token_hash=token_hash(new_token),
            expires_at=func.now() + SANDBOX_LIFE,
        )
        .returning(Sandbox.id)
    ).scalar_one()
    return WriteTarget(sandbox_id, new_token)


def sandbox_cookie(token: str, max_age: int) -> str:
    """The cookie for a sandbox, kept by the browser for the seconds the sandbox has left."""
    return f"{SANDBOX_COOKIE}={token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age={max_age}"


def close_sandbox(engine: Engine, sandbox_id: uuid.UUID) -> None:
    """No later write goes to the sandbox, and the sweep deletes it on its next run."""
    with Session(engine) as session, session.begin():
        session.execute(
            update(Sandbox)
            .where(Sandbox.id == sandbox_id)
            .values(closed_at=func.coalesce(Sandbox.closed_at, func.now()))
        )


def spent_cookie() -> str:
    """Tells the browser to drop its cookie."""
    return f"{SANDBOX_COOKIE}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0"


def seconds_left(engine: Engine, token: str | None) -> int | None:
    """The whole seconds before the cookie's sandbox expires, or None without a live one."""
    with Session(engine) as session:
        sandbox = live_sandbox(session, token)
        if sandbox is None:
            return None
        left = session.scalar(select(func.extract("epoch", sandbox.expires_at - func.now())))
    return max(0, int(left or 0))


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
        # A session-level lock, so the write's own transactions on this connection can commit
        # while it is held, and a write needs one pooled connection, not two.
        connection.execute(select(func.pg_advisory_lock_shared(lock_key(sandbox_id))))
        writable = connection.execute(
            select(Sandbox.id).where(
                Sandbox.id == sandbox_id,
                Sandbox.closed_at.is_(None),
                Sandbox.expires_at > func.now(),
            )
        ).first()
        connection.commit()
    except BaseException:
        connection.invalidate()
        connection.close()
        raise
    if writable is None:
        _release(connection, sandbox_id)
        raise SandboxClosed(str(sandbox_id))
    return connection


def _release(connection: Connection, sandbox_id: uuid.UUID) -> None:
    try:
        connection.rollback()
        connection.execute(select(func.pg_advisory_unlock_shared(lock_key(sandbox_id))))
        connection.commit()
    except BaseException:
        # A connection that may still hold the lock is dropped, never pooled.
        connection.invalidate()
        raise
    finally:
        connection.close()


@asynccontextmanager
async def writing_to(engine: Engine, sandbox_id: uuid.UUID) -> AsyncIterator[Connection]:
    """Hold the sandbox's lock shared for a whole write: the object, the rows and the
    workflow start. "Delete now" takes it exclusively, so it waits for a write already
    under way and deletes what it wrote, and a write that comes after finds the sandbox
    closed and writes nothing. The write's rows go through the connection it yields."""
    connection = await asyncio.to_thread(_take_shared, engine, sandbox_id)
    try:
        yield connection
    finally:
        await asyncio.to_thread(_release, connection, sandbox_id)
