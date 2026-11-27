"""Create sandboxes and make the sandbox part of a document's identity"""

import sqlalchemy as sa
from alembic import op

revision = "e1a5c0d2f7b4"  # pragma: allowlist secret
down_revision = "c3d81f5a7e20"  # pragma: allowlist secret
branch_labels = None
depends_on = None

CHILDREN = ("document_sources", "events", "filed_documents")


def upgrade():
    op.create_table(
        "sandboxes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    for table in ("documents", "filed_documents"):
        op.add_column(table, sa.Column("sandbox_id", sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"{table}_sandbox_id_fkey",
            table,
            "sandboxes",
            ["sandbox_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(op.f(f"ix_{table}_sandbox_id"), table, ["sandbox_id"], unique=False)
        op.drop_constraint(f"{table}_mode_run_id_content_hash_key", table, type_="unique")
        # An empty sandbox counts as equal, so local mode keeps "same bytes, same document".
        op.create_unique_constraint(
            f"{table}_sandbox_id_mode_run_id_content_hash_key",
            table,
            ["sandbox_id", "mode", "run_id", "content_hash"],
            postgresql_nulls_not_distinct=True,
        )
    for table in CHILDREN:
        op.drop_constraint(f"{table}_document_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(
            f"{table}_document_id_fkey",
            table,
            "documents",
            ["document_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade():
    # The old key cannot tell two sandboxes' copies of the same bytes apart, so sandbox
    # documents go first. The cascades below are still in place and take their rows too.
    op.execute("DELETE FROM documents WHERE sandbox_id IS NOT NULL")
    for table in CHILDREN:
        op.drop_constraint(f"{table}_document_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(
            f"{table}_document_id_fkey", table, "documents", ["document_id"], ["id"]
        )
    for table in ("filed_documents", "documents"):
        op.drop_constraint(f"{table}_sandbox_id_mode_run_id_content_hash_key", table, "unique")
        op.create_unique_constraint(
            f"{table}_mode_run_id_content_hash_key", table, ["mode", "run_id", "content_hash"]
        )
        op.drop_index(op.f(f"ix_{table}_sandbox_id"), table_name=table)
        op.drop_constraint(f"{table}_sandbox_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "sandbox_id")
    op.drop_table("sandboxes")
