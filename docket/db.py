import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Sandbox(Base):
    """One browser's private space in the hosted beta. The cookie is never stored, only its
    SHA-256."""

    __tablename__ = "sandboxes"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # An empty sandbox counts as equal, so local mode keeps "same bytes, same document".
        UniqueConstraint(
            "sandbox_id", "mode", "run_id", "content_hash", postgresql_nulls_not_distinct=True
        ),
        CheckConstraint("mode IN ('act', 'observe')"),
        CheckConstraint("mode != 'act' OR run_id = 'live'"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    sandbox_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sandboxes.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="received")
    status_reason: Mapped[str | None] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)
    record_id: Mapped[str | None] = mapped_column(String)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 7))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentSource(Base):
    __tablename__ = "document_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String)
    message_id: Mapped[str | None] = mapped_column(String)
    sender: Mapped[str | None] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    step: Mapped[str] = mapped_column(String)
    idem_key: Mapped[str] = mapped_column(String, unique=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FiledDocument(Base):
    __tablename__ = "filed_documents"
    __table_args__ = (
        UniqueConstraint(
            "sandbox_id", "mode", "run_id", "content_hash", postgresql_nulls_not_distinct=True
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    sandbox_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sandboxes.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String)
    run_id: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    storage_key: Mapped[str] = mapped_column(String, unique=True)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetLedger(Base):
    __tablename__ = "budget_ledger"
    __table_args__ = (CheckConstraint("spent_usd >= 0"),)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)


class SampleCount(Base):
    """Beta samples sent on one UTC day, from every address together."""

    __tablename__ = "sample_counts"
    __table_args__ = (CheckConstraint("count >= 0"),)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class SampleSend(Base):
    """One beta sample sent from an address, stored as its keyed hash. The rows from the last
    60 minutes count toward that address's cap."""

    __tablename__ = "sample_sends"
    id: Mapped[int] = mapped_column(primary_key=True)
    address_hash: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandboxCreation(Base):
    """One new sandbox from an address, stored as its keyed hash. The rows from the last 60
    minutes count toward that address's cap."""

    __tablename__ = "sandbox_creations"
    id: Mapped[int] = mapped_column(primary_key=True)
    address_hash: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BetaFlag(Base):
    """A beta switch that every process reads from the database, so changing it needs no
    restart. A missing row means the switch is in its default position."""

    __tablename__ = "beta_flags"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
