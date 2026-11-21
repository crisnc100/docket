"""The sandbox as part of a document's identity: the key, the cascades and the migration
(plan PR 1, S9)."""

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docket.db import Base, Document, DocumentSource, Event, FiledDocument, Sandbox
from tests.sandboxes import make_sandbox

BASE_REVISION = "c3d81f5a7e20"  # pragma: allowlist secret
HASH = "c" * 64


def document(sandbox_id, **kwargs) -> Document:
    return Document(sandbox_id=sandbox_id, mode="act", run_id="live", content_hash=HASH, **kwargs)


def with_children(session: Session, sandbox_id, record_id="UNIT-0001") -> uuid.UUID:
    doc = document(sandbox_id)
    session.add(doc)
    session.flush()
    session.add(DocumentSource(document_id=doc.id, source="sample"))
    session.add(Event(document_id=doc.id, step="received", idem_key=f"{doc.id}:r", detail={}))
    prefix = f"sbx/{sandbox_id}/" if sandbox_id else ""
    session.add(
        FiledDocument(
            document_id=doc.id,
            sandbox_id=sandbox_id,
            mode="act",
            run_id="live",
            content_hash=HASH,
            record_id=record_id,
            category="lease",
            storage_key=f"{prefix}A/{record_id}/lease/{HASH[:16]}.pdf",
        )
    )
    return doc.id


def counts(engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
            for model in (Sandbox, Document, DocumentSource, Event, FiledDocument)
        }


def test_the_same_bytes_are_one_document_per_sandbox_and_one_without(postgres_engine):
    first, _ = make_sandbox(postgres_engine)
    second, _ = make_sandbox(postgres_engine)
    with Session(postgres_engine) as session:
        session.add_all([document(first), document(second), document(None)])
        session.commit()
        for sandbox_id in (first, second, None):
            session.add(document(sandbox_id))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
        assert session.scalar(select(func.count()).select_from(Document)) == 3


def test_deleting_a_sandbox_row_takes_every_row_of_its_documents(postgres_engine):
    doomed, _ = make_sandbox(postgres_engine)
    kept, _ = make_sandbox(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        with_children(session, doomed)
        with_children(session, kept)
        with_children(session, None)
    assert counts(postgres_engine) == {
        "sandboxes": 2,
        "documents": 3,
        "document_sources": 3,
        "events": 3,
        "filed_documents": 3,
    }
    with Session(postgres_engine) as session, session.begin():
        session.execute(text("DELETE FROM sandboxes WHERE id = :id"), {"id": doomed})
    assert counts(postgres_engine) == {
        "sandboxes": 1,
        "documents": 2,
        "document_sources": 2,
        "events": 2,
        "filed_documents": 2,
    }
    with Session(postgres_engine) as session:
        assert set(session.scalars(select(Document.sandbox_id))) == {kept, None}


def migrate(engine, step, revision: str) -> None:
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        step(config, revision)


def test_the_migration_upgrades_downgrades_and_upgrades_again(postgres_engine):
    sandbox_id, _ = make_sandbox(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        with_children(session, sandbox_id)
        local = with_children(session, None, "UNIT-0002")
    # Down to PR 1's base: the sandbox's document and its rows go first, the local one stays.
    with Session(postgres_engine) as session, session.begin():
        session.execute(text("INSERT INTO sample_sends (address_hash) VALUES (:h)"), {"h": HASH})
    migrate(postgres_engine, command.downgrade, BASE_REVISION)
    tables = set(inspect(postgres_engine).get_table_names())
    assert "sandboxes" not in tables and "sandbox_creations" not in tables
    assert "signin_failures" in tables
    # The old code counts raw addresses, so no hash is left for it to misread.
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM sample_sends")).scalar_one() == 0
        connection.execute(text("SELECT address FROM sample_sends"))
    assert "sandbox_id" not in {
        column["name"] for column in inspect(postgres_engine).get_columns("documents")
    }
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM documents")).scalars().all() == [local]
        for table in ("document_sources", "events", "filed_documents"):
            rows = connection.execute(text(f"SELECT document_id FROM {table}")).scalars().all()
            assert rows == [local], table
        # The old key is back: the same bytes cannot come in twice.
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO documents (id, mode, run_id, content_hash, status) "
                    "VALUES (:id, 'act', 'live', :hash, 'received')"
                ),
                {"id": uuid.uuid4(), "hash": HASH},
            )
    migrate(postgres_engine, command.upgrade, "head")
    with postgres_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with Session(postgres_engine) as session:
        assert session.get(Document, local).sandbox_id is None
    again, _ = make_sandbox(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        with_children(session, again)
    assert counts(postgres_engine)["documents"] == 2
