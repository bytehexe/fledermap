"""rename recording note to device_note, add user note

Revision ID: 7c1a8e2b6f43
Revises: adc5c9197cde
Create Date: 2026-09-26 08:10:00.000000

`Recording.note` (ingested from the file's own GUANO/wamd "Note" field) is renamed to
`device_note` to make room for a new, unrelated `note` column: a user-editable whole-recording
free-text field (supports inline `#tag`s, see domain/tags.py). No data transformation for the
rename -- values move with the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1a8e2b6f43"
down_revision: str | Sequence[str] | None = "adc5c9197cde"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("recording", "note", new_column_name="device_note")
    op.add_column("recording", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "note")
    op.alter_column("recording", "device_note", new_column_name="note")
