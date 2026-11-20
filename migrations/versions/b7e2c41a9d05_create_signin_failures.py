"""Create beta sign-in attempts"""

import sqlalchemy as sa
from alembic import op

revision = "b7e2c41a9d05"  # pragma: allowlist secret
down_revision = "690f4793dd3f"
branch_labels = None
depends_on = None


def upgrade():
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


def downgrade():
    op.drop_index(op.f("ix_signin_failures_address"), table_name="signin_failures")
    op.drop_table("signin_failures")
