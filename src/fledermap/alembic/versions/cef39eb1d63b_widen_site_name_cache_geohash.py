"""widen site_name_cache.geohash

Revision ID: cef39eb1d63b
Revises: 9b5c0b759b40
Create Date: 2026-09-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cef39eb1d63b"
down_revision: str | Sequence[str] | None = "9b5c0b759b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    String(16) -> String(24). `_cache_key` now folds in a radius bucket
    alongside the rounded coordinate (services/site_naming.py, SN-7 fix,
    2026-09-01, `_radius_bucket`) -- worst case "-90.000,-180.000,99990" is
    22 characters, String(16) had zero headroom to begin with. Existing rows
    are untouched; a widen never truncates. (Corrected 2026-09-01, code
    review: an earlier version of this docstring described an abandoned
    target-rank-bucket scheme with a shorter, no longer accurate worst case.)
    """
    op.alter_column(
        "site_name_cache",
        "geohash",
        existing_type=sa.String(length=16),
        type_=sa.String(length=24),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema.

    Narrows back to String(16). Postgres enforces varchar length on
    ALTER COLUMN TYPE (it errors, it does not silently truncate), so this
    correctly refuses if any row was written in the wider (lat,lon,radius
    bucket) format that no longer fits -- SiteNameCache is a pure cache table
    (services/site_naming.py), so the practical remedy is simply to clear it
    first, not to preserve every row across the downgrade.
    """
    op.alter_column(
        "site_name_cache",
        "geohash",
        existing_type=sa.String(length=24),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
