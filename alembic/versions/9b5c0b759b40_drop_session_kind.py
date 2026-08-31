"""drop session kind

Revision ID: 9b5c0b759b40
Revises: 51e72cf104a2
Create Date: 2026-08-29 01:06:33.265105

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b5c0b759b40"
down_revision: str | Sequence[str] | None = "51e72cf104a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("sessionkind", "session", type_="check")
    op.drop_column("session", "kind")
    op.drop_column("session", "kind_locked")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "session",
        sa.Column(
            "kind_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "session",
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="stationary",
        ),
    )
    op.create_check_constraint(
        "sessionkind", "session", "kind IN ('stationary', 'transect')"
    )
