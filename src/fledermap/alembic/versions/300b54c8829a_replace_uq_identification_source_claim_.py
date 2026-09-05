"""replace uq_identification_source_claim with a partial unique index (live rows only)

Revision ID: 300b54c8829a
Revises: f3a1c9d2e4b7
Create Date: 2026-09-05 11:11:42.884632

A plain UniqueConstraint blocked set_manual_classification's own
supersede-then-insert pattern (and services/ingest.py's
_apply_identifications, which uses the same pattern): re-adding a
taxon_id (or the shared-NULL tuple for NO_ID/NOISE) that a row this same
call just superseded collides with that now-superseded row's
still-enforced key tuple, because a plain constraint has no notion of
"superseded, no longer live". Confirmed live, 2026-09-05, when the
classifier box's own primary multi-species workflow (add a second
species chip) crashed with a real UniqueViolation.

Postgres has no partial unique CONSTRAINT syntax; a partial unique INDEX
(scoped to WHERE superseded_at IS NULL) is the standard equivalent --
only currently-live claims participate in the uniqueness check, so a
superseded row's key tuple becomes free to reuse the moment it's
superseded.

NOTE on test_migrations.py's blind spot: `compare_metadata` diffs column
and constraint *types* against models.py, but has no notion of a partial
index's WHERE clause -- mutation-tested 2026-09-05 by temporarily
stripping this migration's `postgresql_where` (making it a plain,
non-partial unique index) and re-running `hatch test tests/test_migrations.py`,
which still PASSED. `test_migrated_partial_index_where_clause_is_enforced`
in tests/test_migrations.py closes this gap by asserting the index's
actual predicate via Postgres's own `pg_indexes.indexdef` catalog column
(string-matching the predicate's text representation -- there is no
`pg_indexes.indpred`; `indpred` lives on `pg_index`, a different catalog
view), the same way this project's existing CHECK-constraint precedent
covers `_comparable`'s analogous blind spot (see CLAUDE.md's Migrations
section).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "300b54c8829a"
down_revision: str | Sequence[str] | None = "f3a1c9d2e4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "uq_identification_source_claim", "identification", type_="unique"
    )
    op.create_index(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "source_version", "raw_label", "taxon_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_identification_source_claim", table_name="identification")
    op.create_unique_constraint(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "source_version", "raw_label", "taxon_id"],
        postgresql_nulls_not_distinct=True,
    )
