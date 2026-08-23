"""SQLAlchemy models. See spec section 5."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from fledermap.domain.codes import IdSource, Verdict


class Base(DeclarativeBase):
    pass


class Recording(Base):
    """One audio file. Identity is `audio_hash`; `path` is mutable (spec D8)."""

    __tablename__ = "recording"

    id: Mapped[int] = mapped_column(primary_key=True)
    audio_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(Text, index=True)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    filename_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timestamp_disagreement_s: Mapped[float | None] = mapped_column(Float)

    geom: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326),
        nullable=True,
    )
    loc_accuracy_m: Mapped[float | None] = mapped_column(Float)
    elevation_m: Mapped[float | None] = mapped_column(Float)

    samplerate_hz: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    te_factor: Mapped[int | None] = mapped_column(Integer)

    make: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    serial: Mapped[str | None] = mapped_column(String(128))
    device: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)

    guano_raw: Mapped[dict] = mapped_column(JSONB, default=dict)

    session_id: Mapped[int | None] = mapped_column(ForeignKey("session.id"))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identifications: Mapped[list[Identification]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Identification(Base):
    """One source's claim. Sources coexist; `superseded_at` records changes of mind."""

    __tablename__ = "identification"
    __table_args__ = (
        UniqueConstraint(
            "recording_id",
            "source",
            "source_version",
            "raw_label",
            name="uq_identification_source_claim",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[IdSource] = mapped_column(String(32))
    source_version: Mapped[str | None] = mapped_column(String(64))
    verdict: Mapped[Verdict] = mapped_column(String(16))
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    raw_label: Mapped[str | None] = mapped_column(String(128))
    confidence: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recording: Mapped[Recording] = relationship(back_populates="identifications")


class Taxon(Base):
    """A species, genus, or phonic group. Not every identification is a species."""

    __tablename__ = "taxon"

    id: Mapped[int] = mapped_column(primary_key=True)
    rank: Mapped[str] = mapped_column(String(16))
    scientific_name: Mapped[str] = mapped_column(String(128), unique=True)
    common_name_de: Mapped[str | None] = mapped_column(String(128))
    common_name_en: Mapped[str | None] = mapped_column(String(128))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))


class TaxonCode(Base):
    """Per-source vocabulary. WA codes are not a universal key (spec D10)."""

    __tablename__ = "taxon_code"
    __table_args__ = (UniqueConstraint("source", "code", name="uq_taxon_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(32), index=True)
    taxon_id: Mapped[int] = mapped_column(ForeignKey("taxon.id"))


class Session(Base):
    """The durable annotation layer. Incremental, never renumbered (spec D7)."""

    __tablename__ = "session"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16), default="stationary")
    detector_key: Mapped[str | None] = mapped_column(String(160), index=True)
    note: Mapped[str | None] = mapped_column(Text)
