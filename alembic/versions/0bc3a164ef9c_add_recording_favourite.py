"""add recording favourite

Revision ID: 0bc3a164ef9c
Revises: cef39eb1d63b
Create Date: 2026-09-01 20:14:13.629410

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0bc3a164ef9c"
down_revision: str | Sequence[str] | None = "cef39eb1d63b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording",
        sa.Column("favourite", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "favourite")
