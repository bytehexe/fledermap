"""initial schema

Revision ID: 7a46d3ce855f
Revises:
Create Date: 2026-08-23 21:02:24.138295

"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a46d3ce855f"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Idempotent, and makes the migration self-sufficient rather than relying
    # on an undocumented manual prerequisite. If the DB role lacks the
    # privilege this fails immediately and visibly.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("detector_key", sa.String(length=160), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_session_detector_key"), "session", ["detector_key"], unique=False
    )
    op.create_index(
        op.f("ix_session_started_at"), "session", ["started_at"], unique=False
    )
    op.create_table(
        "taxon",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.String(length=16), nullable=False),
        sa.Column("scientific_name", sa.String(length=128), nullable=False),
        sa.Column("common_name_de", sa.String(length=128), nullable=True),
        sa.Column("common_name_en", sa.String(length=128), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["taxon.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scientific_name"),
    )
    op.create_table(
        "recording",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("audio_hash", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timestamp_disagreement_s", sa.Float(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
        sa.Column("loc_accuracy_m", sa.Float(), nullable=True),
        sa.Column("elevation_m", sa.Float(), nullable=True),
        sa.Column("samplerate_hz", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("te_factor", sa.Integer(), nullable=True),
        sa.Column("make", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("serial", sa.String(length=128), nullable=True),
        sa.Column("device", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("guano_raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_recording_audio_hash"), "recording", ["audio_hash"], unique=True
    )
    op.create_index(op.f("ix_recording_path"), "recording", ["path"], unique=False)
    op.create_index(
        op.f("ix_recording_recorded_at"), "recording", ["recorded_at"], unique=False
    )
    op.create_table(
        "taxon_code",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("taxon_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "code", name="uq_taxon_code"),
    )
    op.create_index(op.f("ix_taxon_code_code"), "taxon_code", ["code"], unique=False)
    op.create_table(
        "identification",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recording_id", sa.Integer(), nullable=False),
        # source: no CHECK constraint (create_constraint=False) — the design
        # plans further sources (BatDetect2, BattyBirdNET, Kaleidoscope) and a
        # constraint would force a migration for each new classifier.
        sa.Column(
            "source",
            sa.Enum(
                "emt.guano",
                "emt.wamd",
                "emt.filename",
                "batdetect2",
                "battybirdnet",
                "kaleidoscope",
                "manual",
                name="idsource",
                native_enum=False,
                length=32,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(length=64), nullable=True),
        # verdict: closed vocabulary — CHECK constraint enforced.
        sa.Column(
            "verdict",
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
        sa.Column("taxon_id", sa.Integer(), nullable=True),
        sa.Column("raw_label", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["recording_id"], ["recording.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "source",
            "source_version",
            "raw_label",
            name="uq_identification_source_claim",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_identification_recording_id"),
        "identification",
        ["recording_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_identification_recording_id"), table_name="identification")
    op.drop_table("identification")
    op.drop_index(op.f("ix_taxon_code_code"), table_name="taxon_code")
    op.drop_table("taxon_code")
    op.drop_index(op.f("ix_recording_recorded_at"), table_name="recording")
    op.drop_index(op.f("ix_recording_path"), table_name="recording")
    op.drop_index(op.f("ix_recording_audio_hash"), table_name="recording")
    op.drop_table("recording")
    op.drop_table("taxon")
    op.drop_index(op.f("ix_session_started_at"), table_name="session")
    op.drop_index(op.f("ix_session_detector_key"), table_name="session")
    op.drop_table("session")
