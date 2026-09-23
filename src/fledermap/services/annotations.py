"""Comment-annotation CRUD (design spec docs/superpowers/specs/2026-09-23-
fledermap-annotations-design.md §3). The only place `AnnotationSource.COMMENT` rows are ever
written or removed -- a future batdetect2/battybirdnet annotation producer writes/deletes its
own rows directly against the `Annotation` model, never through this module, mirroring how
`services/manual_classification.py` and `services/identifications.py`'s `replace_claims` split
user-written from classifier-written `Identification` rows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource
from fledermap.store.models import Annotation, Recording


def list_annotations(session: OrmSession, recording_id: int) -> Sequence[Annotation]:
    return session.scalars(
        select(Annotation)
        .where(Annotation.recording_id == recording_id)
        .order_by(Annotation.id),
    ).all()


def _require_comment_shape(taxon_id: int | None, literal: str | None) -> None:
    if taxon_id is None and not literal:
        msg = "a comment annotation needs at least one of taxon_id or literal"
        raise ValueError(msg)


def _require_comment_source(annotation: Annotation) -> None:
    if annotation.source != AnnotationSource.COMMENT:
        msg = f"annotation {annotation.id} is source={annotation.source!r}, not comment"
        raise ValueError(msg)


def create_comment_annotation(
    session: OrmSession,
    recording: Recording,
    *,
    time_start_s: float | None,
    time_end_s: float | None,
    freq_min_hz: float | None,
    freq_max_hz: float | None,
    taxon_id: int | None,
    literal: str | None,
) -> Annotation:
    _require_comment_shape(taxon_id, literal)
    annotation = Annotation(
        recording_id=recording.id,
        source=AnnotationSource.COMMENT,
        time_start_s=time_start_s,
        time_end_s=time_end_s,
        freq_min_hz=freq_min_hz,
        freq_max_hz=freq_max_hz,
        taxon_id=taxon_id,
        literal=literal,
        created_at=datetime.now(UTC),
    )
    session.add(annotation)
    session.commit()
    return annotation


def update_comment_annotation(
    session: OrmSession,
    annotation: Annotation,
    *,
    taxon_id: int | None,
    literal: str | None,
) -> None:
    _require_comment_source(annotation)
    _require_comment_shape(taxon_id, literal)
    annotation.taxon_id = taxon_id
    annotation.literal = literal
    session.commit()


def delete_comment_annotation(session: OrmSession, annotation: Annotation) -> None:
    _require_comment_source(annotation)
    session.delete(annotation)
    session.commit()
