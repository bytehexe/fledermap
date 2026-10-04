"""add region tables

Revision ID: a1d4c7e9b2f6
Revises: 7c1a8e2b6f43
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1d4c7e9b2f6"
down_revision: str | Sequence[str] | None = "7c1a8e2b6f43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recording",
        sa.Column("region_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "region",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_distinct", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "location_region",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("location_key", sa.String(length=24), nullable=False),
        sa.Column("region_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_location_region_location_key"),
        "location_region",
        ["location_key"],
        unique=True,
    )
    op.create_table(
        "recording_region",
        sa.Column("recording_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["recording_id"], ["recording.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["region.id"]),
        sa.PrimaryKeyConstraint("recording_id", "region_id"),
    )
    op.create_index(
        op.f("ix_recording_region_region_id"),
        "recording_region",
        ["region_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recording_region_region_id"), table_name="recording_region")
    op.drop_table("recording_region")
    op.drop_index(op.f("ix_location_region_location_key"), table_name="location_region")
    op.drop_table("location_region")
    op.drop_table("region")
    op.drop_column("recording", "region_resolved_at")
