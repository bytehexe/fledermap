"""The map page (design spec section 3/9)."""

from __future__ import annotations

import json

import flask
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.media.preview import TIME_EXPANSION_FACTOR
from fledermap.media.spectrogram import (
    DEFAULT_SPECTROGRAM_PARAMS,
    effective_max_freq_hz,
)
from fledermap.services.current_best import (
    current_best_identification,
    identification_status,
    sort_identifications_by_precedence,
)
from fledermap.services.manual_classification import (
    current_manual_state,
    set_manual_classification,
)
from fledermap.services.map_query import (
    MAX_FEATURES,
    filtered_recordings,
    has_unmapped_species,
    list_sessions,
    list_taxa,
    neighbor_recordings,
    site_detail,
)
from fledermap.services.review_flags import ReviewContext, review_reasons
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site, Taxon
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.params import (
    fallback_site_label,
    parse_bool,
    parse_datetime,
    parse_int,
    parse_taxon_filter,
    parse_verdict,
)
from fledermap.web.views.recording_detail import _taxon_search_index

views_bp = flask.Blueprint("views", __name__, template_folder="../templates")


@views_bp.get("/")
def map_page() -> str:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        return flask.render_template(
            "map.html",
            taxa=list_taxa(session),
            sessions=list_sessions(session),
            has_unmapped_species=has_unmapped_species(session),
            max_features=MAX_FEATURES,
        )


def _render_recording_panel(
    audio_hash: str,
) -> tuple[flask.Response, tuple[float, float] | None]:
    """Shared by the GET panel route and the favourite-toggle POST below --
    both build the exact same fragment from the exact same filter query
    string (`flask.request.args` reads the query string regardless of
    method), so the two routes can never drift on what "the current panel"
    looks like. Returns the point too, but does NOT set the
    recording-selected HX-Trigger itself -- only a fresh GET navigation
    should re-pan/zoom the map; toggling favourite on an already-open panel
    must not."""
    try:
        date_from = parse_datetime(flask.request.args.get("from"))
        date_to = parse_datetime(flask.request.args.get("to"), end_of_day=True)
        taxon_id = parse_taxon_filter(flask.request.args.get("taxon"))
        taxon_exclude = parse_bool(flask.request.args.get("taxon_exclude"))
        verdict = parse_verdict(flask.request.args.get("verdict"))
        session_id = parse_int(flask.request.args.get("session"))
        site_id = parse_int(flask.request.args.get("site"))
        source_raw = flask.request.args.get("source")
        source = IdSource(source_raw) if source_raw else None
        favourite_only = parse_bool(flask.request.args.get("favourite_only"))
        needs_review_only = parse_bool(flask.request.args.get("needs_review_only"))
    except ValueError as exc:
        return flask.make_response((str(exc), 400)), None

    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    filter_qs = flask.request.query_string.decode()

    with OrmSession(engine) as session:
        recordings = filtered_recordings(
            session,
            date_from=date_from,
            date_to=date_to,
            taxon_id=taxon_id,
            taxon_exclude=taxon_exclude,
            verdict=verdict,
            session_id=session_id,
            site_id=site_id,
            source=source,
            favourite_only=favourite_only,
            needs_review=needs_review_only,
        )
        neighbors = neighbor_recordings(recordings, audio_hash)
        if neighbors is None:
            html = flask.render_template("_recording_panel.html", found=False)
            return flask.make_response(html), None

        previous, following = neighbors
        recording = next(r for r in recordings if r.audio_hash == audio_hash)
        best = current_best_identification(recording)
        review_context = ReviewContext.build(session)
        reasons = review_reasons(recording, review_context)
        taxon = None
        if best is not None and not best.is_multi and best.primary.taxon_id is not None:
            taxon = session.get(Taxon, best.primary.taxon_id)
        identifications_with_status = [
            (ident, identification_status(ident, best))
            for ident in sort_identifications_by_precedence(recording.identifications)
        ]
        point = decode_point(recording.geom)

        recording_session = (
            session.get(AnnotationSession, recording.session_id)
            if recording.session_id
            else None
        )
        site = session.get(Site, recording.site_id) if recording.site_id else None
        site_label = None
        if site is not None:
            site_label = (
                site.name
                if site.name
                else fallback_site_label(decode_point(site.centroid))
            )

        max_freq_khz = None
        if recording.samplerate_hz:
            max_freq_khz = (
                effective_max_freq_hz(
                    recording.samplerate_hz,
                    DEFAULT_SPECTROGRAM_PARAMS,
                )
                / 1000
            )

        html = flask.render_template(
            "_recording_panel.html",
            found=True,
            recording=recording,
            best=best,
            reasons=reasons,
            taxon=taxon,
            identifications_with_status=identifications_with_status,
            previous=previous,
            next=following,
            filter_qs=filter_qs,
            spectrogram_ready=spectrogram_path(media_root, audio_hash).exists(),
            oscillogram_ready=oscillogram_path(media_root, audio_hash).exists(),
            preview_ready=preview_path(media_root, audio_hash).exists(),
            recording_session=recording_session,
            site=site,
            site_label=site_label,
            duration_s=recording.duration_s,
            max_freq_khz=max_freq_khz,
            time_expansion_factor=TIME_EXPANSION_FACTOR,
        )

    return flask.make_response(html), point


@views_bp.get("/recordings/<audio_hash>/panel")
def recording_panel(audio_hash: str) -> flask.Response:
    response, point = _render_recording_panel(audio_hash)
    if point is not None:
        response.headers["HX-Trigger"] = json.dumps(
            {
                "recording-selected": {
                    "hash": audio_hash,
                    "latitude": point[1],
                    "longitude": point[0],
                },
            },
        )
    return response


@views_bp.post("/recordings/<audio_hash>/favourite")
def toggle_favourite(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            return flask.make_response(("Recording not found.", 404))
        recording.favourite = not recording.favourite
        session.commit()

        # The standalone recording-detail page's favourite button is its own small
        # fragment (`_detail_favourite_button.html`), not the drawer's filter-qs/prev-next
        # `_recording_panel.html` -- distinguished by this query param so the two callers
        # (drawer panel, detail page) share one toggle endpoint instead of forking into a
        # duplicate route.
        if flask.request.args.get("panel") == "detail":
            html = flask.render_template(
                "_detail_favourite_button.html",
                recording=recording,
            )
            return flask.make_response(html)

    response, _point = _render_recording_panel(audio_hash)
    return response


@views_bp.post("/recordings/<audio_hash>/flag-for-review")
def toggle_flag_for_review(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            return flask.make_response(("Recording not found.", 404))
        recording.flagged_for_review = not recording.flagged_for_review
        session.commit()

        if flask.request.args.get("panel") == "detail":
            reasons = review_reasons(recording, ReviewContext.build(session))
            html = flask.render_template(
                "_detail_flag_button.html",
                recording=recording,
                reasons=reasons,
            )
            return flask.make_response(html)

    response, _point = _render_recording_panel(audio_hash)
    return response


@views_bp.post("/recordings/<audio_hash>/manual-classification")
def post_manual_classification(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            return flask.make_response(("Recording not found.", 404))

        try:
            verdict_raw = flask.request.form.get("verdict")
            verdict = Verdict(verdict_raw) if verdict_raw else None
            taxon_ids = [int(v) for v in flask.request.form.getlist("taxon_ids") if v]
            set_manual_classification(
                session,
                recording,
                verdict=verdict,
                taxon_ids=taxon_ids,
            )
        except ValueError as exc:
            return flask.make_response((str(exc), 400))

        # The classifier box's own state -- deliberately NOT `best`
        # (current_best_identification's cross-source precedence result);
        # see current_manual_state's docstring for why.
        manual_verdict, manual_taxon_ids = current_manual_state(recording)
        current_taxa = []
        if manual_taxon_ids:
            current_taxa = list(
                session.scalars(
                    select(Taxon)
                    .where(Taxon.id.in_(manual_taxon_ids))
                    .order_by(Taxon.scientific_name),
                ).all(),
            )

        html = flask.render_template(
            "_classifier_box.html",
            recording=recording,
            manual_verdict=manual_verdict,
            current_taxa=current_taxa,
            taxon_search_index=_taxon_search_index(session),
        )
        response = flask.make_response(html)

    return response


@views_bp.get("/sites/<int:site_id>/panel")
def site_panel(site_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        detail = site_detail(session, site_id)
        if detail is None:
            html = flask.render_template("_site_panel.html", found=False)
            return flask.make_response(html)
        point = decode_point(detail.site.centroid)
        label = detail.site.name if detail.site.name else fallback_site_label(point)
        html = flask.render_template(
            "_site_panel.html",
            found=True,
            detail=detail,
            label=label,
        )

    response = flask.make_response(html)
    if point is not None:
        response.headers["HX-Trigger"] = json.dumps(
            {
                "site-selected": {
                    "id": site_id,
                    "latitude": point[1],
                    "longitude": point[0],
                    "radius_m": detail.site.radius_m,
                },
            },
        )
    return response
