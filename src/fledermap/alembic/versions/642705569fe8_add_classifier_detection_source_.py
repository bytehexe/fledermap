"""add classifier_detection source recording_id index

Revision ID: 642705569fe8
Revises: 3f93ba5bb5cf
Create Date: 2026-09-16 19:30:35.038658

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "642705569fe8"
down_revision: str | Sequence[str] | None = "3f93ba5bb5cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_classifier_detection_source_recording_id",
        "classifier_detection",
        ["source", "recording_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_classifier_detection_source_recording_id",
        table_name="classifier_detection",
    )
