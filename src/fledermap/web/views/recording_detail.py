"""The standalone recording details page (design spec
2026-09-01-fledermap-recording-details-page-design.md, section 3) -- a full
page, not an HTMX drawer fragment, matching `sessions.py`'s own precedent
for a detail view that deserves the whole screen rather than the drawer's
small, drag-resized panel."""

from __future__ import annotations

import flask
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource
from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.media.paths import preview_path
from fledermap.media.preview import TIME_EXPANSION_FACTOR
from fledermap.services.annotations import (
    create_label_annotation,
    delete_label_annotation,
    list_annotations,
    update_label_annotation,
)
from fledermap.services.classify_implications import lookup_mined_suggestions
from fledermap.services.classify_suggestions import (
    ClassifySuggestion,
    classify_suggestions,
)
from fledermap.services.current_best import (
    CurrentIdentification,
    IdentificationMatrix,
    current_best_identification,
    identification_matrix,
)
from fledermap.services.manual_classification import current_manual_state
from fledermap.services.recording_detail import (
    detail_params,
)
from fledermap.services.review_flags import (
    ReviewContext,
    parse_review_snapshot,
    resolve_review_snapshot,
    review_reasons,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Annotation, Recording, Site, Taxon, TaxonCode
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.params import fallback_site_label, is_safe_relative_path

recording_detail_bp = flask.Blueprint(
    "recording_detail",
    __name__,
    template_folder="../templates",
)


def _taxon_search_index(session: OrmSession) -> list[dict[str, object]]:
    """One entry per Taxon, searched client-side against scientific_name,
    both common names, and every mapped TaxonCode.code -- small enough
    (~70 taxa) to inline as JSON rather than a per-keystroke endpoint,
    matching this project's existing "no frontend build step" scale
    assumption (design spec §5)."""
    taxa = session.scalars(select(Taxon)).all()
    codes_by_taxon: dict[int, list[str]] = {}
    for code in session.scalars(
        select(TaxonCode).where(TaxonCode.source != RANGE_CODE_SOURCE),
    ).all():
        codes_by_taxon.setdefault(code.taxon_id, []).append(code.code)
    return [
        {
            "id": t.id,
            "scientific_name": t.scientific_name,
            "common_name_en": t.common_name_en,
            "common_name_de": t.common_name_de,
            "codes": codes_by_taxon.get(t.id, []),
        }
        for t in taxa
    ]


def _serialize_classify_suggestion(s: ClassifySuggestion) -> dict[str, object]:
    return {
        "taxa": [{"id": t.id, "scientific_name": t.scientific_name} for t in s.taxa],
        "is_noise": s.is_noise,
        "label": s.label,
        # Unused by the client (`classifier_logic.js` re-filters purely on taxa/is_noise) --
        # kept so a test asserting on an embedded suggestion can tell part (a)'s own
        # evidence-driven Noise heuristic (`_PRIORITY_NOISE`) apart from the unconditional
        # Noise-if-room pool-assembly fallback (priority 0.0), which look identical
        # (is_noise=True, label="Noise") without it.
        "priority": s.priority,
    }


def _classify_suggestions_data(
    session: OrmSession, recording: Recording
) -> list[dict[str, object]]:
    """Shared by the details page's initial render and the manual-classification save's
    `_classifier_box.html` fragment re-render (`web/views/map.py`'s
    `post_manual_classification`) -- both must embed the same suggestion pool."""
    mined = lookup_mined_suggestions(session, recording)
    suggestions = classify_suggestions(
        recording,
        noise_snr_threshold_db=flask.current_app.config["NOISE_SNR_THRESHOLD_DB"],
        mined=mined,
    )
    return [_serialize_classify_suggestion(s) for s in suggestions]


def _serialize_annotation(a: Annotation) -> dict[str, object]:
    return {
        "id": a.id,
        "source": a.source.value,
        "time_start_s": a.time_start_s,
        "time_end_s": a.time_end_s,
        "freq_min_hz": a.freq_min_hz,
        "freq_max_hz": a.freq_max_hz,
        "taxon_id": a.taxon_id,
        "confidence": a.confidence,
        "literal": a.literal,
    }


_DEFAULT_BACK_LINK = ("Back to map", "/")


def _resolve_back_link(return_to: str | None) -> tuple[str, str]:
    """Turn a caller-supplied `return_to` (a page's own path+query, e.g. the
    map drawer's `/?{filter_qs}`) into a (label, url) pair for the details
    page's back link -- backlog note "Back to map": different text per
    origin, falling back to the map when the origin is missing or not one we
    recognise. Anything not a safe same-origin relative path (see
    `is_safe_relative_path`) is rejected rather than sending the user
    off-site."""
    if return_to is None or not is_safe_relative_path(return_to):
        return _DEFAULT_BACK_LINK
    if return_to == "/" or return_to.startswith("/?"):
        return ("Back to map", return_to)
    if return_to == "/sessions" or return_to.startswith("/sessions/"):
        return ("Back to sessions", return_to)
    return ("Back", return_to)


def _current_classification(
    session: OrmSession, recording: Recording
) -> tuple[CurrentIdentification | None, Taxon | None, IdentificationMatrix]:
    """What the page's headline and identifications table show: the recording's current-best
    identification, its resolved taxon (single-taxon results only) and the classifier matrix.
    Shared by the details page and by the Classify box's save response, which re-renders the
    headline and the table -- both derive from this, so the two can never disagree."""
    best = current_best_identification(recording)
    taxon = None
    if best is not None and not best.is_multi and best.primary.taxon_id is not None:
        taxon = session.get(Taxon, best.primary.taxon_id)
    matrix = identification_matrix(
        recording.identifications,
        best,
        combine_emt=flask.current_app.config["COMBINE_EMT_DISPLAY"],
    )
    return best, taxon, matrix


@recording_detail_bp.get("/recordings/<audio_hash>")
def recording_details_page(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            flask.abort(404)

        best, taxon, matrix = _current_classification(session, recording)
        reasons = review_reasons(recording, ReviewContext.build(session))
        taxon_search_index = _taxon_search_index(session)
        suggestions = _classify_suggestions_data(session, recording)
        annotations = [
            _serialize_annotation(a) for a in list_annotations(session, recording.id)
        ]

        # The classifier box's own state -- deliberately NOT derived from
        # `best` (see current_manual_state's docstring).
        manual_verdict, manual_taxon_ids = current_manual_state(recording)
        current_taxa: list[Taxon] = []
        if manual_taxon_ids:
            current_taxa = list(
                session.scalars(
                    select(Taxon)
                    .where(Taxon.id.in_(manual_taxon_ids))
                    .order_by(Taxon.scientific_name),
                ).all(),
            )

        site = session.get(Site, recording.site_id) if recording.site_id else None
        site_label = None
        if site is not None:
            site_label = (
                site.name
                if site.name
                else fallback_site_label(decode_point(site.centroid))
            )

        recording_session = (
            session.get(AnnotationSession, recording.session_id)
            if recording.session_id
            else None
        )

        params = None
        if recording.duration_s is not None and recording.samplerate_hz is not None:
            params = detail_params(recording.duration_s, recording.samplerate_hz)

        # Separate from `params` on purpose: `params` only needs `duration_s`/
        # `samplerate_hz` (available immediately from the WAV header at ingest),
        # while the audio preview is a derived-media job that can still be
        # pending -- ingested-but-not-yet-processed is a normal, if narrow,
        # timing window. The map drawer's `_recording_panel.html` already
        # gates its own audio controls on this (`preview_ready`,
        # `web/views/map.py`); this page didn't, so it could show a fully
        # working-looking TE/HET/play toolbar that 404'd the instant you
        # clicked play (Janna, 2026-09-04, live use, caught right after a
        # PREVIEW_VERSION bump made the gap wide: "Map page says audio
        # preview is not processed yet - details page shows all the
        # buttons. Inconsistent").
        preview_ready = preview_path(media_root, audio_hash).exists()

        back_label, back_url = _resolve_back_link(flask.request.args.get("return_to"))

        review_ids = parse_review_snapshot(flask.request.args.get("review"))
        previous = following = None
        is_review_session = False
        review_position: tuple[int, int] | None = None
        review_qs = ""
        if review_ids:
            ordered = resolve_review_snapshot(session, review_ids)
            index = next(
                (i for i, r in enumerate(ordered) if r.id == recording.id),
                None,
            )
            if index is not None:
                is_review_session = True
                review_position = (index + 1, len(ordered))
                previous = ordered[index - 1] if index > 0 else None
                following = ordered[index + 1] if index < len(ordered) - 1 else None
                review_qs = ",".join(str(i) for i in review_ids)

        html = flask.render_template(
            "recording_details.html",
            recording=recording,
            best=best,
            reasons=reasons,
            taxon=taxon,
            current_taxa=current_taxa,
            manual_verdict=manual_verdict,
            taxon_search_index=taxon_search_index,
            suggestions=suggestions,
            annotations=annotations,
            classifier_thresholds=flask.current_app.config["CLASSIFIER_THRESHOLDS"],
            classifier_display_thresholds=flask.current_app.config[
                "CLASSIFIER_DISPLAY_THRESHOLDS"
            ],
            matrix=matrix,
            site=site,
            site_label=site_label,
            recording_session=recording_session,
            duration_s=recording.duration_s,
            params=params,
            preview_ready=preview_ready,
            time_expansion_factor=TIME_EXPANSION_FACTOR,
            back_label=back_label,
            back_url=back_url,
            previous=previous,
            next=following,
            review_qs=review_qs,
            is_review_session=is_review_session,
            review_position=review_position,
        )
    return flask.make_response(html)


def _find_recording_or_404(session: OrmSession, audio_hash: str) -> Recording:
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one_or_none()
    if recording is None:
        flask.abort(404)
    return recording


def _find_label_annotation_or_404(
    session: OrmSession,
    recording: Recording,
    annotation_id: int,
) -> Annotation:
    annotation = session.get(Annotation, annotation_id)
    if annotation is None or annotation.recording_id != recording.id:
        flask.abort(404)
    if annotation.source != AnnotationSource.LABEL:
        flask.abort(404)
    return annotation


@recording_detail_bp.post("/recordings/<audio_hash>/annotations")
def post_annotation(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    body = flask.request.get_json(silent=True) or {}
    with OrmSession(engine) as session:
        recording = _find_recording_or_404(session, audio_hash)
        try:
            annotation = create_label_annotation(
                session,
                recording,
                time_start_s=body.get("time_start_s"),
                time_end_s=body.get("time_end_s"),
                freq_min_hz=body.get("freq_min_hz"),
                freq_max_hz=body.get("freq_max_hz"),
                taxon_id=body.get("taxon_id"),
                literal=body.get("literal"),
            )
        except ValueError as exc:
            return flask.make_response((flask.jsonify({"error": str(exc)}), 400))
        return flask.jsonify(_serialize_annotation(annotation))


@recording_detail_bp.patch("/recordings/<audio_hash>/annotations/<int:annotation_id>")
def patch_annotation(audio_hash: str, annotation_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    body = flask.request.get_json(silent=True) or {}
    with OrmSession(engine) as session:
        recording = _find_recording_or_404(session, audio_hash)
        annotation = _find_label_annotation_or_404(session, recording, annotation_id)
        try:
            update_label_annotation(
                session,
                annotation,
                taxon_id=body.get("taxon_id"),
                literal=body.get("literal"),
            )
        except ValueError as exc:
            return flask.make_response((flask.jsonify({"error": str(exc)}), 400))
        return flask.jsonify(_serialize_annotation(annotation))


@recording_detail_bp.delete("/recordings/<audio_hash>/annotations/<int:annotation_id>")
def delete_annotation(audio_hash: str, annotation_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = _find_recording_or_404(session, audio_hash)
        annotation = _find_label_annotation_or_404(session, recording, annotation_id)
        delete_label_annotation(session, annotation)
        return flask.jsonify({"ok": True})


@recording_detail_bp.post("/recordings/<audio_hash>/note")
def save_recording_note(audio_hash: str) -> flask.Response:
    note = flask.request.form.get("note") or None
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = _find_recording_or_404(session, audio_hash)
        recording.note = note
        session.commit()
    return flask.make_response(
        flask.redirect(f"/recordings/{audio_hash}?saved=1#note-section"),
    )
