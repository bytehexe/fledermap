"""phase 2 derivation schema

Revision ID: e9a0c0f92971
Revises: 7a46d3ce855f
Create Date: 2026-08-24 21:39:54.215040

"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9a0c0f92971"
down_revision: str | Sequence[str] | None = "7a46d3ce855f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "site",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "centroid",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=False,
        ),
        sa.Column("radius_m", sa.Float(), nullable=False),
        sa.Column("recording_count", sa.Integer(), nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("admin_path", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # No explicit spatial index here, matching `recording.geom` in 0001:
    # geoalchemy2 creates the GIST index itself on table creation (spatial_index
    # defaults to True on `Geography`), outside alembic's own DDL. Adding one
    # explicitly here would create a second, colliding index of the same name.
    op.create_table(
        "site_name_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("geohash", sa.String(length=16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("admin_path", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_site_name_cache_geohash"),
        "site_name_cache",
        ["geohash"],
        unique=True,
    )
    op.create_table(
        "session_merge_proposal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_a_id", sa.Integer(), nullable=False),
        sa.Column("session_b_id", sa.Integer(), nullable=False),
        sa.Column("bridging_recording_id", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        # resolution: closed vocabulary (open == NULL) — CHECK constraint
        # enforced, same pattern as `verdict` and `session.kind`.
        sa.Column(
            "resolution",
            sa.Enum(
                "merged",
                "rejected",
                name="mergeresolution",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["bridging_recording_id"], ["recording.id"]),
        sa.ForeignKeyConstraint(["session_a_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["session_b_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("recording", sa.Column("site_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "recording_site_id_fkey",
        "recording",
        "site",
        ["site_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("session", sa.Column("weather", sa.Text(), nullable=True))
    op.add_column("session", sa.Column("effort", sa.Text(), nullable=True))
    # `kind`'s column type (VARCHAR(16)) does not change, so alembic's own
    # autogenerate never proposes this: sqla_compat.all_table_check_constraints
    # drops _type_bound constraints from its comparison (see
    # tests/test_migrations.py's `_enum_check_constraints`), so the CHECK a
    # non-native `Enum` with `create_constraint=True` adds is invisible to the
    # diff on an *existing* column. Added here by hand; phase-2 fix, task 5.
    op.create_check_constraint(
        "sessionkind", "session", "kind IN ('stationary', 'transect')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("sessionkind", "session", type_="check")
    op.drop_column("session", "effort")
    op.drop_column("session", "weather")
    op.drop_constraint("recording_site_id_fkey", "recording", type_="foreignkey")
    op.drop_column("recording", "site_id")
    op.drop_table("session_merge_proposal")
    op.drop_index(op.f("ix_site_name_cache_geohash"), table_name="site_name_cache")
    op.drop_table("site_name_cache")
    op.drop_table("site")
