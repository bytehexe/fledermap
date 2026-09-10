"""add recording flagged for review

Revision ID: c7a64b0561f1
Revises: ef85421bf57d
Create Date: 2026-09-10 07:28:42.300294

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a64b0561f1"
down_revision: str | Sequence[str] | None = "ef85421bf57d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording",
        sa.Column(
            "flagged_for_review",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "flagged_for_review")
