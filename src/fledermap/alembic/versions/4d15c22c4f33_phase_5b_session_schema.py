"""phase 5b session schema

Revision ID: 4d15c22c4f33
Revises: e9a0c0f92971
Create Date: 2026-08-27 08:20:12.650901

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d15c22c4f33"
down_revision: str | Sequence[str] | None = "e9a0c0f92971"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("session", "effort")
    op.add_column(
        "session",
        sa.Column(
            "kind_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("session", "kind_locked")
    op.add_column("session", sa.Column("effort", sa.Text(), nullable=True))
