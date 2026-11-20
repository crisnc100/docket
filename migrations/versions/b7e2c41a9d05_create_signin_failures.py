"""Create beta sign-in failure counts"""

import sqlalchemy as sa
from alembic import op

revision = "b7e2c41a9d05"
down_revision = "690f4793dd3f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "signin_failures",
        sa.Column("address", sa.String(), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.CheckConstraint("failures >= 0"),
        sa.PrimaryKeyConstraint("address", "hour"),
    )


def downgrade():
    op.drop_table("signin_failures")
