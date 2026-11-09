import uuid
from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docket.db import Base, Document, DocumentSource, Event, FiledDocument


def document(**kwargs):
    return Document(mode="act", run_id="live", content_hash="a" * 64, **kwargs)


def test_migration_matches_metadata_and_downgrades(postgres_engine):
    assert set(inspect(postgres_engine).get_table_names()) == {
        "alembic_version",
        "documents",
        "document_sources",
        "events",
        "filed_documents",
        "budget_ledger",
    }
    with postgres_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    config = Config("alembic.ini")
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert inspect(postgres_engine).get_table_names() == ["alembic_version"]


def test_document_dedup_is_per_mode_and_run(postgres_engine):
    with Session(postgres_engine) as session:
        session.add(document())
        session.commit()
        session.add(document())
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add_all(
            [
                Document(mode="observe", run_id=run, content_hash="a" * 64)
                for run in ["eval-7", "eval-11"]
            ]
        )
        session.commit()
        assert session.query(Document).count() == 3


@pytest.mark.parametrize("mode,run", [("wrong", "live"), ("act", "not-live")])
def test_mode_constraints(postgres_engine, mode, run):
    with Session(postgres_engine) as session:
        session.add(Document(mode=mode, run_id=run, content_hash="a" * 64))
        with pytest.raises(IntegrityError):
            session.commit()


def test_sources_events_and_filing_constraints(postgres_engine):
    with Session(postgres_engine) as session:
        doc = document(score=Decimal("0.875"))
        session.add(doc)
        session.flush()
        session.add_all(
            [
                DocumentSource(document_id=doc.id, source="cli", message_id=message)
                for message in ["first", "resubmit"]
            ]
        )
        session.add(
            Event(
                document_id=doc.id,
                step="received",
                idem_key=f"{doc.id}:received",
                detail={"source": {"message_id": "first"}},
            )
        )
        session.add(
            FiledDocument(
                document_id=doc.id,
                mode="act",
                run_id="live",
                content_hash=doc.content_hash,
                record_id="UNIT-0001",
                category="lease",
                storage_key="A/UNIT-0001/lease/a.pdf",
            )
        )
        session.commit()
        assert doc.created_at.tzinfo is not None
        assert session.query(DocumentSource).count() == 2
        assert session.query(Event).one().detail["source"]["message_id"] == "first"
        session.add(
            Event(document_id=doc.id, step="received", idem_key=f"{doc.id}:received", detail={})
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(
            FiledDocument(
                document_id=doc.id,
                mode="act",
                run_id="live",
                content_hash=doc.content_hash,
                record_id="UNIT-0002",
                category="notice",
                storage_key="B/UNIT-0002/notice/a.pdf",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_foreign_keys(postgres_engine):
    with Session(postgres_engine) as session:
        session.add(DocumentSource(document_id=uuid.uuid4(), source="cli"))
        with pytest.raises(IntegrityError):
            session.commit()
