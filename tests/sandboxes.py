"""Sandboxes made directly in the database, for tests that need one before any write."""

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, insert
from sqlalchemy.orm import Session

from docket.db import Document, Event, Sandbox
from docket.sandbox import SANDBOX_COOKIE, token_hash


def make_sandbox(
    engine, *, minutes_left: float = 50, closed: bool = False, age_minutes: float = 0
) -> tuple[uuid.UUID, str]:
    """A sandbox and its cookie value. A negative minutes_left makes an expired one."""
    token = secrets.token_urlsafe(16)
    with Session(engine) as session, session.begin():
        sandbox_id = session.execute(
            insert(Sandbox)
            .values(
                id=uuid.uuid4(),
                token_hash=token_hash(token),
                created_at=func.now() - timedelta(minutes=age_minutes),
                expires_at=func.now() + timedelta(minutes=minutes_left),
                closed_at=func.now() if closed else None,
            )
            .returning(Sandbox.id)
        ).scalar_one()
    return sandbox_id, token


def sandbox_cookie(token: str) -> str:
    return f"{SANDBOX_COOKIE}={token}"


def held_in(engine, sandbox_id: uuid.UUID | None, candidates=("UNIT-0001", "UNIT-0002")):
    """A document waiting for review in the sandbox, with the candidates its score offers."""
    with Session(engine) as session, session.begin():
        document = Document(
            sandbox_id=sandbox_id,
            mode="act",
            run_id="live",
            content_hash=secrets.token_hex(32),
            status="needs_review",
            status_reason="insufficient_corroboration",
        )
        session.add(document)
        session.flush()
        session.add(
            Event(
                document_id=document.id,
                step="score",
                idem_key=f"{document.id}:score",
                detail={"candidates": [{"record_id": item} for item in candidates]},
            )
        )
        return document.id
