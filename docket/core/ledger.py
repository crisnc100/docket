import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from docket.db import Document, Event

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class Status(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    FILED = "filed"
    DISMISSED = "dismissed"
    NEEDS_REVIEW_EXPIRED = "needs_review_expired"
    WOULD_FILE = "would_file"
    WOULD_REVIEW = "would_review"
    REJECTED = "rejected"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset(
    {
        Status.FILED,
        Status.DISMISSED,
        Status.NEEDS_REVIEW_EXPIRED,
        Status.WOULD_FILE,
        Status.WOULD_REVIEW,
        Status.REJECTED,
        Status.FAILED,
    }
)
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.RECEIVED: frozenset({Status.PROCESSING, Status.REJECTED, Status.FAILED}),
    Status.PROCESSING: frozenset(
        {
            Status.FILED,
            Status.NEEDS_REVIEW,
            Status.WOULD_FILE,
            Status.WOULD_REVIEW,
            Status.REJECTED,
            Status.FAILED,
        }
    ),
    Status.NEEDS_REVIEW: frozenset(
        {Status.FILED, Status.DISMISSED, Status.NEEDS_REVIEW_EXPIRED, Status.FAILED}
    ),
}
REJECTION_REASONS = frozenset(
    {"no_attachment", "unsupported_type", "zero_byte", "encrypted", "too_large", "corrupt"}
)


class InvalidTransition(ValueError):
    pass


class DocumentNotFound(LookupError):
    pass


def append_event(
    session: Session, document_id: uuid.UUID, step: str, detail: dict[str, JsonValue]
) -> bool:
    if re.fullmatch(r"[a-z][a-z0-9_]*", step) is None:
        raise ValueError("Event step must be a lowercase identifier")
    event_id = session.execute(
        insert(Event)
        .values(document_id=document_id, step=step, idem_key=f"{document_id}:{step}", detail=detail)
        .on_conflict_do_nothing(index_elements=[Event.idem_key])
        .returning(Event.id)
    ).scalar_one_or_none()
    return event_id is not None


def transition(session: Session, document_id: uuid.UUID, target: Status | str, reason: str) -> bool:
    """Caller owns the transaction; status and its event commit or roll back together."""
    document = session.get(Document, document_id, with_for_update=True, populate_existing=True)
    if document is None:
        raise DocumentNotFound(str(document_id))
    try:
        current, destination = Status(document.status), Status(target)
    except ValueError as exc:
        raise InvalidTransition("Unknown document status") from exc
    if current == destination:
        return False
    if destination not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"Transition {current} -> {destination} is not allowed")
    if not reason.strip():
        raise InvalidTransition("A non-empty status reason is required")
    if destination == Status.REJECTED and reason not in REJECTION_REASONS:
        raise InvalidTransition("Unknown rejection reason")
    if document.mode == "observe" and destination in {
        Status.FILED,
        Status.NEEDS_REVIEW,
        Status.DISMISSED,
        Status.NEEDS_REVIEW_EXPIRED,
    }:
        raise InvalidTransition("Observe mode cannot file or wait for review")
    if document.mode == "act" and destination in {Status.WOULD_FILE, Status.WOULD_REVIEW}:
        raise InvalidTransition("Act mode cannot use observe terminal statuses")
    document.status = destination.value
    document.status_reason = reason
    document.updated_at = datetime.now(UTC)
    append_event(
        session,
        document_id,
        destination.value,
        {"from": current.value, "to": destination.value, "reason": reason},
    )
    session.flush()
    return True
