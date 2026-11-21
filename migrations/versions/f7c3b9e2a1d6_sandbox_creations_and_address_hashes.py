"""Count new sandboxes, store address hashes, and drop the sign-in attempts"""

import sqlalchemy as sa
from alembic import op

revision = "f7c3b9e2a1d6"  # pragma: allowlist secret
down_revision = "e1a5c0d2f7b4"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index(op.f("ix_signin_failures_address"), table_name="signin_failures")
    op.drop_table("signin_failures")
    op.create_table(
        "sandbox_creations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("address_hash", sa.String(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sandbox_creations_address_hash"),
        "sandbox_creations",
        ["address_hash"],
        unique=False,
    )
    # The rows written so far hold raw addresses.
    op.execute("DELETE FROM sample_sends")
    op.alter_column("sample_sends", "address", new_column_name="address_hash")
    op.drop_index(op.f("ix_sample_sends_address"), table_name="sample_sends")
    op.create_index(
        op.f("ix_sample_sends_address_hash"), "sample_sends", ["address_hash"], unique=False
    )


def downgrade():
    # The old code counts raw addresses, so the hashes go.
    op.execute("DELETE FROM sample_sends")
    op.drop_index(op.f("ix_sample_sends_address_hash"), table_name="sample_sends")
    op.alter_column("sample_sends", "address_hash", new_column_name="address")
    op.create_index(op.f("ix_sample_sends_address"), "sample_sends", ["address"], unique=False)
    op.drop_index(op.f("ix_sandbox_creations_address_hash"), table_name="sandbox_creations")
    op.drop_table("sandbox_creations")
    op.create_table(
        "signin_failures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_signin_failures_address"), "signin_failures", ["address"], unique=False
    )
