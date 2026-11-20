"""Create beta sample counts, sample sends and beta flags"""

import sqlalchemy as sa
from alembic import op

revision = "c3d81f5a7e20"  # pragma: allowlist secret
down_revision = "b7e2c41a9d05"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sample_counts",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint("count >= 0"),
        sa.PrimaryKeyConstraint("day"),
    )
    op.create_table(
        "sample_sends",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sample_sends_address"), "sample_sends", ["address"], unique=False)
    op.create_table(
        "beta_flags",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("value", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade():
    op.drop_table("beta_flags")
    op.drop_index(op.f("ix_sample_sends_address"), table_name="sample_sends")
    op.drop_table("sample_sends")
    op.drop_table("sample_counts")
