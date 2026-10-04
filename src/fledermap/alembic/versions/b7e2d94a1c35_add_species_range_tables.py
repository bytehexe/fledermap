"""add species range tables

Revision ID: b7e2d94a1c35
Revises: a1d4c7e9b2f6
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "b7e2d94a1c35"
down_revision: str | Sequence[str] | None = "a1d4c7e9b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "species_range",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("taxon_id", sa.Integer(), nullable=False),
        sa.Column("presence", sa.SmallInteger(), nullable=False),
        sa.Column("origin", sa.SmallInteger(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geography(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                dimension=2,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # No explicit spatial index: geoalchemy2 creates the GIST index itself on table creation
    # (see e9a0c0f92971), and an explicit one would collide with it.
    op.create_index(
        op.f("ix_species_range_taxon_id"),
        "species_range",
        ["taxon_id"],
        unique=False,
    )
    op.create_table(
        "species_range_fetch",
        sa.Column("taxon_id", sa.Integer(), nullable=False),
        sa.Column("query_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_version", sa.String(length=16), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("taxon_id"),
    )


def downgrade() -> None:
    op.drop_table("species_range_fetch")
    op.drop_index(op.f("ix_species_range_taxon_id"), table_name="species_range")
    op.drop_table("species_range")
