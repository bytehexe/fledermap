"""drop identification superseded_at, plain unique constraint

Revision ID: ef85421bf57d
Revises: 300b54c8829a
Create Date: 2026-09-06 13:08:38.964658

Replaces the partial unique index from migration 300b54c8829a with a plain
UniqueConstraint now that services/identifications.py's replace_claims never
soft-deletes -- see docs/superpowers/specs/2026-09-06-fledermap-drop-
identification-supersede-design.md.

Deletes every row where superseded_at IS NOT NULL before dropping the column
-- left in place, those rows would become indistinguishable from live claims
the instant nothing can filter on superseded_at any more.

BACKUP FIRST: run scripts/db-backup.sh against the real database before
applying this migration there (`alembic upgrade head`) -- this migration
deletes data. This is a deploy-time step, not part of this project's
automated test suite (which only ever runs migrations against a disposable
testcontainer).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef85421bf57d"
down_revision: str | Sequence[str] | None = "300b54c8829a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM identification WHERE superseded_at IS NOT NULL")
    op.drop_index("uq_identification_source_claim", table_name="identification")
    op.drop_column("identification", "superseded_at")
    op.create_unique_constraint(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "taxon_id"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Downgrade schema.

    Cannot restore deleted rows or which claims were superseded before this
    migration ran -- downgrading recreates the column and the old partial
    index shape, but every row comes back with superseded_at=NULL (i.e.
    "live"), which is data loss relative to pre-upgrade state. Acceptable for
    a dev-only downgrade path; never run this against a database whose
    upgrade already deleted real superseded rows and expect them back.
    """
    op.drop_constraint(
        "uq_identification_source_claim", "identification", type_="unique"
    )
    op.add_column(
        "identification",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "source_version", "raw_label", "taxon_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
