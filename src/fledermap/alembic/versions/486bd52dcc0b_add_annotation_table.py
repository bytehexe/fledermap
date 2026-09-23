"""add annotation table

Revision ID: 486bd52dcc0b
Revises: 642705569fe8
Create Date: 2026-09-23 07:48:12.899985

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "486bd52dcc0b"
down_revision: str | Sequence[str] | None = "642705569fe8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "annotation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recording_id", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "comment",
                "batdetect2",
                "battybirdnet",
                name="annotationsource",
                native_enum=False,
                length=32,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("time_start_s", sa.Float(), nullable=True),
        sa.Column("time_end_s", sa.Float(), nullable=True),
        sa.Column("freq_min_hz", sa.Float(), nullable=True),
        sa.Column("freq_max_hz", sa.Float(), nullable=True),
        sa.Column("taxon_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("literal", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "time_start_s IS NOT NULL OR freq_min_hz IS NOT NULL",
            name="ck_annotation_has_bound",
        ),
        sa.ForeignKeyConstraint(["recording_id"], ["recording.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_annotation_recording_id"),
        "annotation",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_annotation_source"),
        "annotation",
        ["source"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_annotation_source"), table_name="annotation")
    op.drop_index(op.f("ix_annotation_recording_id"), table_name="annotation")
    op.drop_table("annotation")
