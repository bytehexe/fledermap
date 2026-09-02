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
    """Downgrade schema.

    Every existing row backfills to 'stationary'/not-locked — any human
    classification that had been recorded is not recoverable; the values
    exist nowhere else once `upgrade()` has run. Accepted because this
    schema's only consumer was removed in the same change that removes it
    here (see the design spec's rationale).
    """
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
    # Drop the server_default now that it has done its one job (backfilling
    # existing rows) -- the original column (0001_initial.py) never had one;
    # new INSERTs are expected to supply `kind` explicitly (or rely on the
    # ORM's Python-side `default=`, which only applies client-side).
    op.alter_column("session", "kind", server_default=None)
    op.create_check_constraint(
        "sessionkind", "session", "kind IN ('stationary', 'transect')"
    )
