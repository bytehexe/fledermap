"""SQLAlchemy models. See spec section 5."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
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
    AnnotationSource,
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
    snr_db: Mapped[float | None] = mapped_column(Float)
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

    # Fingerprint of the file's content-relevant disk state the last time it
    # was FULLY scanned (content read, hashed, SNR computed) -- not merely
    # resolved. `services/ingest.py`'s `scan_all_roots` uses these to skip
    # that expensive work entirely for a file whose current (size, mtime)
    # still matches: see docs/superpowers/specs/2026-09-16-fledermap-worker-
    # scaling-design.md §3.1. A stale/mismatching pair only ever costs one
    # extra full rescan of that one file -- correctness never depends on
    # these being right, only performance does.
    scan_size_bytes: Mapped[int | None] = mapped_column(Integer)
    scan_mtime: Mapped[float | None] = mapped_column(Float)
    favourite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flagged_for_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    identifications: Mapped[list[Identification]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
        lazy="selectin",
        # Deterministic order, not just insertion-order-by-luck: `id` is the only
        # column guaranteed to break ties, since `set_manual_classification`
        # stamps every row of one save with the same `first_seen_at`, and without
        # this `CurrentIdentification.primary`'s documented first-added tie-break
        # (and the Identifications box's on-page row order) would depend on
        # whatever order Postgres happens to return rows in.
        order_by="Identification.id",
    )


class Identification(Base):
    """One source's claim. Sources coexist; a claim a source no longer makes
    is deleted, not kept around (services/identifications.py's
    replace_claims)."""

    __tablename__ = "identification"
    __table_args__ = (
        # Plain constraint, not a partial index: with replace_claims
        # (services/identifications.py) never soft-deleting, there is no
        # "superseded, still occupying the key" row left to collide with --
        # the collision this constraint's predecessor (a partial unique
        # INDEX scoped to WHERE superseded_at IS NULL, see migration
        # 300b54c8829a) existed to work around cannot happen any more. See
        # docs/superpowers/specs/2026-09-06-fledermap-drop-identification-
        # supersede-design.md.
        UniqueConstraint(
            "recording_id",
            "source",
            "taxon_id",
            name="uq_identification_source_claim",
            # Postgres treats NULLs as distinct by default -- without this,
            # the NO_ID/NOISE sentinel claim (taxon_id IS NULL) could have
            # unlimited duplicate live rows per (recording, source).
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

    recording: Mapped[Recording] = relationship(back_populates="identifications")
    # No back_populates -- nothing needs the reverse (Taxon -> every claim
    # that ever referenced it). Read-only convenience for resolving a row's
    # display label (services/current_best.py's identification_label).
    taxon: Mapped[Taxon | None] = relationship()


class ClassifierDetection(Base):
    """Raw classifier output, independent of the currently configured verdict
    threshold -- the durable record of "what did the model actually say."
    `Identification` rows are materialized from this table by filtering on the
    current threshold and calling `services/identifications.py`'s
    `replace_claims`; this table itself is never read by `current_best.py` or
    any other identification consumer. See docs/superpowers/specs/2026-09-15-
    fledermap-batdetect2-classifier-design.md §2.

    `source` has no CHECK constraint, mirroring `Identification.source`
    (spec D9): further classifiers are coming, and a CHECK would force a
    migration per one. This table is deliberately generic (not
    `batdetect2_detection`) so a future classifier reuses it without a new
    migration.

    Holds every candidate above a fixed, non-configurable, low sanity floor
    (see jobs/tasks.py's `_BATDETECT2_FLOOR`) -- not the user-configured
    verdict threshold. A finished run with no candidate at all holds one NoID marker row
    (`services/classifier_detections.py`'s `NO_DETECTION_LABEL`), so "found nothing" is
    distinguishable from "not run yet". Exposing the floor as config would let someone set it
    above their own threshold and silently break threshold-change recompute.
    """

    __tablename__ = "classifier_detection"
    __table_args__ = (
        # Backs the NOT EXISTS anti-join in services/batdetect2.py's
        # enqueue_batdetect2 and services/battybirdnet.py's
        # enqueue_battybirdnet -- both filter by (source, recording_id)
        # every ingest cycle to find recordings still missing a detection
        # row. Without this, Postgres falls back to a full Seq Scan of this
        # table (confirmed via EXPLAIN ANALYZE: 12.2s at 334k rows) even
        # though `source` and `recording_id` each have their OWN index --
        # neither alone can drive an index-only anti-join on the pair.
        Index(
            "ix_classifier_detection_source_recording_id",
            "source",
            "recording_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[IdSource] = mapped_column(
        SAEnum(
            IdSource,
            native_enum=False,
            length=32,
            create_constraint=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        index=True,
    )
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    # The model's own label for this candidate, kept verbatim whether or not
    # it resolved to a taxon. An unresolved label is not a failure -- it is
    # carried into the materialized claim's `raw_label` so the recording shows
    # as "unmapped species" and can be reconnected later by adding a code
    # mapping, exactly like an EMT source's unmapped code. Without it, an
    # unmapped detection would be indistinguishable from "the model said
    # nothing", and the model is never re-run once a detection row exists.
    raw_label: Mapped[str | None] = mapped_column(String(128))
    confidence: Mapped[float] = mapped_column(Float)

    recording: Mapped[Recording] = relationship()
    taxon: Mapped[Taxon | None] = relationship()


class Annotation(Base):
    """A user- or classifier-produced marker pointing at a place in a recording -- a time range,
    a frequency band, a point, or a rectangle of both. See docs/superpowers/specs/2026-09-23-
    fledermap-annotations-design.md.

    `source` has no CHECK constraint, mirroring `Identification.source`/
    `ClassifierDetection.source`: more producers are coming, a CHECK would force a migration per
    one.

    Geometry is four independently-nullable bound columns rather than a separate shape/kind
    column -- a point is `time_start_s == time_end_s` (and/or `freq_min_hz == freq_max_hz`); no
    read code infers intent from which columns are null vs. which are equal, both are meaningful.
    `ck_annotation_has_bound` rejects the one truly invalid state: every bound null, an annotation
    pointing at nothing.

    `taxon_id`/`confidence`/`literal` are all nullable on every source. For a `label` row,
    `services/annotations.py`'s `create_label_annotation`/`update_label_annotation` enforce
    that at least one of `taxon_id`/`literal` is set (an annotation naming neither is pointless
    data) -- not a DB constraint, since a future non-label source may legitimately have
    neither. `confidence` is classifier-only; nothing writes it for a `label` row.
    """

    __tablename__ = "annotation"
    __table_args__ = (
        CheckConstraint(
            "time_start_s IS NOT NULL OR freq_min_hz IS NOT NULL",
            name="ck_annotation_has_bound",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[AnnotationSource] = mapped_column(
        SAEnum(
            AnnotationSource,
            native_enum=False,
            length=32,
            create_constraint=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        index=True,
    )
    time_start_s: Mapped[float | None] = mapped_column(Float)
    time_end_s: Mapped[float | None] = mapped_column(Float)
    freq_min_hz: Mapped[float | None] = mapped_column(Float)
    freq_max_hz: Mapped[float | None] = mapped_column(Float)
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    confidence: Mapped[float | None] = mapped_column(Float)
    literal: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    recording: Mapped[Recording] = relationship()
    taxon: Mapped[Taxon | None] = relationship()


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
    """A derived cluster of species-identified, GPS-bearing recordings — a
    projection, not an entity.

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
    # Widened from String(16) (migration 2026-09-01): _cache_key folds in a
    # radius bucket alongside the coordinate, since the resolved name now
    # depends on the querying site's own radius too
    # (services/site_naming.py's `_radius_bucket`) -- worst case
    # "-90.000,-180.000,99990" is 22 chars, String(16) had zero headroom to
    # begin with.
    geohash: Mapped[str] = mapped_column(String(24), unique=True, index=True)
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


class ClassifyImplicationRule(Base):
    """One mined association rule for the Classify box's "likely suggestions" feature, part
    (b) (docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-part-b-design.md).
    Rebuilt from scratch by services/classify_implications.py's mine_classify_implications on
    every mining run -- never incrementally updated (spec §4/§6)."""

    __tablename__ = "classify_implication_rule"
    __table_args__ = (
        # Postgres treats NULLs as distinct by default -- without this, a NO_ID/NOISE
        # conclusion (conclusion_taxon_id IS NULL) for the same antecedent_key could get
        # duplicate rows across two mining runs that raced, same gotcha as
        # Identification.uq_identification_source_claim.
        UniqueConstraint(
            "antecedent_key",
            "conclusion_verdict",
            "conclusion_taxon_id",
            name="uq_classify_implication_rule_antecedent_conclusion",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_classify_implication_rule_antecedent_key", "antecedent_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Sorted taxon_ids, comma-joined (e.g. "1" or "1,24") -- see
    # services/classify_implications.py's antecedent_key() for the canonical form.
    # String(64) is generous headroom for the size-2 cap: two 9-digit ids plus a
    # separator is 19 chars.
    antecedent_key: Mapped[str] = mapped_column(String(64))
    conclusion_verdict: Mapped[Verdict] = mapped_column(
        SAEnum(
            Verdict,
            native_enum=False,
            length=16,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
    )
    # Set only when conclusion_verdict == SPECIES.
    conclusion_taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    support: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    lift: Mapped[float] = mapped_column(Float)
