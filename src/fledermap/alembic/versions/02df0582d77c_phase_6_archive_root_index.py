"""phase 6 archive root index

Revision ID: 02df0582d77c
Revises: 4d15c22c4f33
Create Date: 2026-08-28 08:15:33.900619

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "02df0582d77c"
down_revision: str | Sequence[str] | None = "4d15c22c4f33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording",
        sa.Column(
            "archive_root_index",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "archive_root_index")
