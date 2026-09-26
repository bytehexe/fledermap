"""rename annotation source comment to label

Revision ID: adc5c9197cde
Revises: 06ac66feb141
Create Date: 2026-09-26 05:40:00.000000

Data-only migration: `AnnotationSource.COMMENT` was renamed to `AnnotationSource.LABEL`
(value "comment" -> "label"). `source` has `create_constraint=False`
(spec D9-equivalent reasoning: further classifiers are coming), so there is no CHECK to
alter -- only existing rows' stored strings need to move.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "adc5c9197cde"
down_revision: str | Sequence[str] | None = "06ac66feb141"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE annotation SET source = 'label' WHERE source = 'comment'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE annotation SET source = 'comment' WHERE source = 'label'")
