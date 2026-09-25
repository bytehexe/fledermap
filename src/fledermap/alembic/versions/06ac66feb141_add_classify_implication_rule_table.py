"""add classify implication rule table

Revision ID: 06ac66feb141
Revises: 486bd52dcc0b
Create Date: 2026-09-25 15:02:39.068456

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "06ac66feb141"
down_revision: str | Sequence[str] | None = "486bd52dcc0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "classify_implication_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("antecedent_key", sa.String(length=64), nullable=False),
        sa.Column(
            "conclusion_verdict",
            sa.Enum(
                "species",
                "no_id",
                "noise",
                name="verdict",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("conclusion_taxon_id", sa.Integer(), nullable=True),
        sa.Column("support", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("lift", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["conclusion_taxon_id"], ["taxon.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "antecedent_key",
            "conclusion_verdict",
            "conclusion_taxon_id",
            name="uq_classify_implication_rule_antecedent_conclusion",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_classify_implication_rule_antecedent_key"),
        "classify_implication_rule",
        ["antecedent_key"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_classify_implication_rule_antecedent_key"),
        table_name="classify_implication_rule",
    )
    op.drop_table("classify_implication_rule")
