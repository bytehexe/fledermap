"""add recording scan fingerprint columns

Revision ID: 3f93ba5bb5cf
Revises: 0e45f9fc01af
Create Date: 2026-09-16 19:19:18.820144

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f93ba5bb5cf"
down_revision: str | Sequence[str] | None = "0e45f9fc01af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording", sa.Column("scan_size_bytes", sa.Integer(), nullable=True)
    )
    op.add_column("recording", sa.Column("scan_mtime", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "scan_mtime")
    op.drop_column("recording", "scan_size_bytes")
