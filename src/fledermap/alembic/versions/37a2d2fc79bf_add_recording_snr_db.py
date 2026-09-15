"""add recording snr_db

Revision ID: 37a2d2fc79bf
Revises: c7a64b0561f1
Create Date: 2026-09-15 12:33:42.039226

Broadband SNR (dB), computed at ingest time from the recording's own audio
(`ingest/snr.py`, `maad.sound.spectral_snr`) -- Step 1 of the Obsidian backlog
"Improve data quality" item, just displaying the value; a noise classifier
built on top of it is a later step. Nullable: existing recordings ingested
before this shipped have no value until `fledermap backfill-snr` runs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "37a2d2fc79bf"
down_revision: str | Sequence[str] | None = "c7a64b0561f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("recording", sa.Column("snr_db", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "snr_db")
