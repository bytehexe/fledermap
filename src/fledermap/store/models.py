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
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from fledermap.domain.codes import (
    IdSource,
    MergeResolution,
    Verdict,
    VisualSighting,
)


class Base(DeclarativeBase):
    pass


class Recording(Base):
    """One audio file. Identity is `audio_hash`; `path` is mutable (spec D8)."""

    __tablename__ = "recording"

    id: Mapped[int] = mapped_column(primary_key=True)
    audio_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(Text, index=True)

    # Which configured `Config.archive_roots[i]` this file was scanned under
    # (design spec §3). Read-time media resolution (`jobs/tasks.py`) is
    # `archive_roots[archive_root_index] / path` -- exact, no search. Default
    # 0 (ORM-level, not just the migration's server_default) because several
    # test helpers across the suite construct `Recording(...)` directly
    # without setting it (e.g. `tests/test_jobs_tasks.py`'s `_make_recording`).
    archive_root_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

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
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("site.id", ondelete="SET NULL"),
    )
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
            # Postgres treats NULLs as distinct by default, so without this a
            # source that reports no version (filename IDs, manual annotations)
            # could insert unlimited duplicates of the same claim.
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"),
        index=True,
    )
    # `source` is deliberately open: the design plans BatDetect2, BattyBirdNET
    # and Kaleidoscope as further sources. A CHECK constraint would force a
    # schema migration for every new classifier, so keep the typing and skip
    # the constraint.
    #
    # values_callable: SQLAlchemy's Enum type persists the member *name*
    # (e.g. "EMT_WAMD") by default, not `.value` (e.g. "emt.wamd"). Our
    # StrEnum's dotted-lowercase values are the canonical on-disk vocabulary
    # (a plain `String` column previously stored exactly those, since a
    # StrEnum instance *is* its value as a string) — without this the switch
    # to `Enum` would silently rewrite that representation.
    source: Mapped[IdSource] = mapped_column(
        SAEnum(
            IdSource,
            native_enum=False,
            length=32,
            create_constraint=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
    )
    source_version: Mapped[str | None] = mapped_column(String(64))
    # `verdict` is a closed vocabulary — species / no_id / noise. A CHECK
    # constraint is correct here and will not need revisiting.
    #
    # create_constraint=True: this SQLAlchemy version defaults
    # `create_constraint` to False, so it must be requested explicitly or no
    # constraint is emitted at all — confirmed by inspecting the DDL.
    verdict: Mapped[Verdict] = mapped_column(
        SAEnum(
            Verdict,
            native_enum=False,
            length=16,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
    )
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
    detector_key: Mapped[str | None] = mapped_column(String(160), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    weather: Mapped[str | None] = mapped_column(Text)
    # Whether a human observer saw a bat visually during the session --
    # independent of acoustic detection/species ID (2026-08-28). Purely
    # user-set -- nothing here ever auto-classifies it. Defaults to UNCLEAR
    # ("we don't know") for both new and pre-existing sessions.
    seen_visually: Mapped[VisualSighting] = mapped_column(
        SAEnum(
            VisualSighting,
            native_enum=False,
            length=16,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=VisualSighting.UNCLEAR,
    )


class Site(Base):
    """A derived cluster of stationary recordings — a projection, not an entity.

    Deleted and rebuilt wholesale by `services.derive.derive_sites` (spec
    section 7) — `DELETE`, never `TRUNCATE`; see that function's docstring.
    `radius_m` is the true extent of all `recording_count` members: no outlier
    trimming is applied, DBSCAN having already decided membership.
    `name`/`admin_path` are schema now, populated by Phase 3's poiidx naming
    job — this phase never writes them.
    """

    __tablename__ = "site"

    id: Mapped[int] = mapped_column(primary_key=True)
    centroid: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326),
    )
    radius_m: Mapped[float] = mapped_column(Float)
    recording_count: Mapped[int] = mapped_column(Integer)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    name: Mapped[str | None] = mapped_column(Text)
    admin_path: Mapped[str | None] = mapped_column(Text)


class SiteNameCache(Base):
    """Keyed on rounded coordinates; survives site rebuilds so re-derivation
    never re-triggers a Geofabrik download (spec section 7). Unused until
    Phase 3."""

    __tablename__ = "site_name_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    geohash: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(Text)
    admin_path: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionMergeProposal(Base):
    """A bridging recording connected two already-persisted sessions.

    Never auto-merged (spec section 7) — this row is what a future UI (Phase 5)
    surfaces for a human to accept or reject.
    """

    __tablename__ = "session_merge_proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_a_id: Mapped[int] = mapped_column(ForeignKey("session.id"))
    session_b_id: Mapped[int] = mapped_column(ForeignKey("session.id"))
    bridging_recording_id: Mapped[int] = mapped_column(ForeignKey("recording.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[MergeResolution | None] = mapped_column(
        SAEnum(
            MergeResolution,
            native_enum=False,
            length=16,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
    )
