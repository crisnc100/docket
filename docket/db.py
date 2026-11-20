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


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("mode", "run_id", "content_hash"),
        CheckConstraint("mode IN ('act', 'observe')"),
        CheckConstraint("mode != 'act' OR run_id = 'live'"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
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
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    source: Mapped[str] = mapped_column(String)
    message_id: Mapped[str | None] = mapped_column(String)
    sender: Mapped[str | None] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    step: Mapped[str] = mapped_column(String)
    idem_key: Mapped[str] = mapped_column(String, unique=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FiledDocument(Base):
    __tablename__ = "filed_documents"
    __table_args__ = (UniqueConstraint("mode", "run_id", "content_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
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


class SigninFailure(Base):
    """One beta sign-in attempt. A right code deletes its row, so the rows left in the last
    hour are the wrong codes that count toward the lockout."""

    __tablename__ = "signin_failures"
    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SampleCount(Base):
    """Beta samples sent in one window: one row per address per hour, and one row per UTC day
    for all addresses together."""

    __tablename__ = "sample_counts"
    __table_args__ = (CheckConstraint("count >= 0"),)
    scope: Mapped[str] = mapped_column(String, primary_key=True)
    window: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class BetaFlag(Base):
    """A beta switch that every process reads from the database, so changing it needs no
    restart. A missing row means the switch is in its default position."""

    __tablename__ = "beta_flags"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
