"""add classifier_detection table

Revision ID: 0e45f9fc01af
Revises: 37a2d2fc79bf
Create Date: 2026-09-15 23:45:12.508535

Raw classifier output, independent of the currently configured verdict threshold --
see docs/superpowers/specs/2026-09-15-fledermap-batdetect2-classifier-design.md §2.
`source` has no CHECK constraint, mirroring `identification.source` (spec D9):
further classifiers are coming and a CHECK would force a migration per one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0e45f9fc01af"
down_revision: str | Sequence[str] | None = "37a2d2fc79bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "classifier_detection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recording_id", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "emt.guano",
                "emt.wamd",
                "emt.filename",
                "emt.manual",
                "batdetect2",
                "battybirdnet",
                "kaleidoscope",
                "manual",
                "fledermap.noise",
                name="idsource",
                native_enum=False,
                length=32,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("taxon_id", sa.Integer(), nullable=True),
        sa.Column("raw_label", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["recording_id"], ["recording.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_classifier_detection_recording_id"),
        "classifier_detection",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_classifier_detection_source"),
        "classifier_detection",
        ["source"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_classifier_detection_source"),
        table_name="classifier_detection",
    )
    op.drop_index(
        op.f("ix_classifier_detection_recording_id"),
        table_name="classifier_detection",
    )
    op.drop_table("classifier_detection")
