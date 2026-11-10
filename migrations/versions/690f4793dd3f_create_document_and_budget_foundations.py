"""Create document and budget foundations"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "690f4793dd3f"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "budget_ledger",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("spent_usd", sa.Numeric(precision=18, scale=9), nullable=False),
        sa.CheckConstraint("spent_usd >= 0"),
        sa.PrimaryKeyConstraint("day"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("status_reason", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("record_id", sa.String(), nullable=True),
        sa.Column("score", sa.Numeric(precision=8, scale=7), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("mode != 'act' OR run_id = 'live'"),
        sa.CheckConstraint("mode IN ('act', 'observe')"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mode", "run_id", "content_hash"),
    )
    op.create_table(
        "document_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("sender", sa.String(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_document_sources_document_id"), "document_sources", ["document_id"], unique=False
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("step", sa.String(), nullable=False),
        sa.Column("idem_key", sa.String(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idem_key"),
    )
    op.create_index(op.f("ix_events_document_id"), "events", ["document_id"], unique=False)
    op.create_table(
        "filed_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column(
            "filed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mode", "run_id", "content_hash"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        op.f("ix_filed_documents_document_id"), "filed_documents", ["document_id"], unique=False
    )


def downgrade():
    op.drop_index(op.f("ix_filed_documents_document_id"), table_name="filed_documents")
    op.drop_table("filed_documents")
    op.drop_index(op.f("ix_events_document_id"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_document_sources_document_id"), table_name="document_sources")
    op.drop_table("document_sources")
    op.drop_table("documents")
    op.drop_table("budget_ledger")
