"""add taxon_id to identification unique constraint

Revision ID: f3a1c9d2e4b7
Revises: 0bc3a164ef9c
Create Date: 2026-09-05 00:00:00.000000

A genuine multi-species MANUAL result (fledermap-manual-classification,
2026-09-05) needs several `identification` rows sharing the same
(recording_id, source, source_version, raw_label) -- source_version and
raw_label are both NULL for every manual row -- differing only in
`taxon_id`. Without `taxon_id` in `uq_identification_source_claim`,
`postgresql_nulls_not_distinct=True` made a second manual SPECIES claim on a
recording collide with the first regardless of which taxon it named,
confirmed by a real `UniqueViolation` when testing the multi-species read
path. `taxon_id` staying nullable and covered by the same
NULLS NOT DISTINCT clause preserves the existing "MANUAL NO_ID/NOISE stay a
singleton" behavior (both still have `taxon_id IS NULL`), while now allowing
distinct-taxon MANUAL SPECIES rows to coexist.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1c9d2e4b7"
down_revision: str | Sequence[str] | None = "0bc3a164ef9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "uq_identification_source_claim",
        "identification",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "source_version", "raw_label", "taxon_id"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_identification_source_claim",
        "identification",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "source_version", "raw_label"],
        postgresql_nulls_not_distinct=True,
    )
