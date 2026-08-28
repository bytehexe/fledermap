"""add session seen_visually

Revision ID: 51e72cf104a2
Revises: 02df0582d77c
Create Date: 2026-08-28 18:20:22.671314

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "51e72cf104a2"
down_revision: str | Sequence[str] | None = "02df0582d77c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Brand new column -- create_constraint=True on a freshly created column
    # (not retrofitted onto an existing one) emits its CHECK as part of this
    # same DDL, same as `session_merge_proposal.resolution` at its own table's
    # creation. Unlike `session.kind`'s CHECK (added by hand in a later
    # migration, e9a0c0f92971), no separate op.create_check_constraint is
    # needed here.
    op.add_column(
        "session",
        sa.Column(
            "seen_visually",
            sa.Enum(
                "yes",
                "no",
                "unclear",
                name="visualsighting",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
            server_default="unclear",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("session", "seen_visually")
