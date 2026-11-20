"""Create beta sample counts and beta flags"""

import sqlalchemy as sa
from alembic import op

revision = "c3d81f5a7e20"  # pragma: allowlist secret
down_revision = "b7e2c41a9d05"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sample_counts",
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("window", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint("count >= 0"),
        sa.PrimaryKeyConstraint("scope", "window"),
    )
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
    op.drop_table("sample_counts")
