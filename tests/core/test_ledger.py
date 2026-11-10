import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docket.core.ledger import (
    TERMINAL_STATUSES,
    DocumentNotFound,
    InvalidTransition,
    Status,
    append_event,
    transition,
)
from docket.db import Document, Event


@pytest.fixture()
def document_id(postgres_engine):
    with Session(postgres_engine) as session, session.begin():
        document = Document(mode="act", run_id="live", content_hash="a" * 64)
        session.add(document)
        session.flush()
        return document.id


def set_status(engine, document_id, status, mode="act"):
    with Session(engine) as session, session.begin():
        doc = session.get(Document, document_id)
        doc.status = status
        doc.mode = mode
        doc.run_id = "live" if mode == "act" else "eval-test"
        doc.status_reason = "previous reason"


def test_events_are_idempotent_and_original_detail_is_immutable(postgres_engine, document_id):
    with Session(postgres_engine) as session, session.begin():
        assert append_event(session, document_id, "choose", {"choice": "UNIT-0001"})
        assert not append_event(session, document_id, "choose", {"choice": "UNIT-0002"})
        assert append_event(
            session, document_id, "score", {"score": 0.8, "signals": ["id", "address"]}
        )
    with Session(postgres_engine) as session:
        events = session.scalars(select(Event).order_by(Event.id)).all()
        assert len(events) == 2
        assert events[0].idem_key == f"{document_id}:choose"
        assert events[0].detail == {"choice": "UNIT-0001"}
        assert events[0].at.tzinfo is not None


def test_concurrent_event_append_has_one_winner(postgres_engine, document_id):
    def append(_):
        with Session(postgres_engine) as session, session.begin():
            return append_event(session, document_id, "file", {"key": "unchanged"})

    with ThreadPoolExecutor(max_workers=5) as executor:
        assert sum(executor.map(append, range(5))) == 1
    with Session(postgres_engine) as session:
        assert session.query(Event).count() == 1


def test_status_and_event_rollback_together(postgres_engine, document_id):
    with Session(postgres_engine) as session:
        assert transition(session, document_id, Status.PROCESSING, "started")
        session.rollback()
    with Session(postgres_engine) as session:
        assert session.get(Document, document_id).status == "received"
        assert session.query(Event).count() == 0


def test_status_retry_does_not_change_terminal_reason_or_event(postgres_engine, document_id):
    with Session(postgres_engine) as session, session.begin():
        assert transition(session, document_id, Status.PROCESSING, "started")
        assert transition(session, document_id, Status.FILED, "matched")
        assert not transition(session, document_id, Status.FILED, "different retry reason")
    with Session(postgres_engine) as session:
        assert session.get(Document, document_id).status_reason == "matched"
        assert session.query(Event).count() == 2


def test_whitelist_all_status_pairs(postgres_engine, document_id):
    allowed = {
        "received": {"processing", "rejected", "failed"},
        "processing": {"filed", "needs_review", "would_file", "would_review", "rejected", "failed"},
        "needs_review": {"filed", "dismissed", "needs_review_expired", "failed"},
    }
    assert TERMINAL_STATUSES == {
        Status.FILED,
        Status.DISMISSED,
        Status.NEEDS_REVIEW_EXPIRED,
        Status.WOULD_FILE,
        Status.WOULD_REVIEW,
        Status.REJECTED,
        Status.FAILED,
    }
    for current in Status:
        for target in Status:
            mode = "observe" if target in {Status.WOULD_FILE, Status.WOULD_REVIEW} else "act"
            set_status(postgres_engine, document_id, current.value, mode)
            with Session(postgres_engine) as session:
                reason = "zero_byte" if target == Status.REJECTED else "test reason"
                if current == target:
                    assert not transition(session, document_id, target, reason)
                elif target.value in allowed.get(current.value, set()):
                    assert transition(session, document_id, target, reason)
                    doc = session.get(Document, document_id)
                    assert doc.status == target.value and doc.status_reason == reason
                else:
                    with pytest.raises(InvalidTransition):
                        transition(session, document_id, target, reason)
                session.rollback()


@pytest.mark.parametrize(
    "mode,target",
    [
        ("observe", "filed"),
        ("observe", "needs_review"),
        ("act", "would_file"),
        ("act", "would_review"),
    ],
)
def test_mode_routes_cannot_cross(postgres_engine, document_id, mode, target):
    set_status(postgres_engine, document_id, "processing", mode)
    with Session(postgres_engine) as session:
        with pytest.raises(InvalidTransition):
            transition(session, document_id, target, "test")


@pytest.mark.parametrize(
    "target,reason", [("processing", "  "), ("rejected", "unknown"), ("unknown_status", "test")]
)
def test_invalid_status_and_reason(postgres_engine, document_id, target, reason):
    with Session(postgres_engine) as session:
        with pytest.raises(InvalidTransition):
            transition(session, document_id, target, reason)


def test_unknown_persisted_status_is_rejected(postgres_engine, document_id):
    set_status(postgres_engine, document_id, "broken_status")
    with Session(postgres_engine) as session:
        with pytest.raises(InvalidTransition, match="Unknown"):
            transition(session, document_id, "failed", "test")


def test_missing_document_is_explicit(postgres_engine):
    with Session(postgres_engine) as session:
        with pytest.raises(DocumentNotFound):
            transition(session, uuid.uuid4(), "failed", "orphaned")
        with pytest.raises(IntegrityError):
            append_event(session, uuid.uuid4(), "file", {})


@pytest.mark.parametrize("step", ["", "choose:other", "../file", "MixedCase", "file event"])
def test_invalid_event_step_rejected(postgres_engine, document_id, step):
    with Session(postgres_engine) as session:
        with pytest.raises(ValueError, match="identifier"):
            append_event(session, document_id, step, {})
