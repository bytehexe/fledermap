"""The map page (design spec section 3/9)."""

from __future__ import annotations

import json

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.media.paths import preview_path, spectrogram_path
from fledermap.services.current_best import current_best_identification
from fledermap.services.map_query import (
    filtered_recordings,
    list_sessions,
    list_taxa,
    neighbor_recordings,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Taxon
from fledermap.web.params import parse_datetime, parse_int, parse_verdict

views_bp = flask.Blueprint("views", __name__, template_folder="../templates")


@views_bp.get("/")
def map_page() -> str:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        return flask.render_template(
            "map.html",
            taxa=list_taxa(session),
            sessions=list_sessions(session),
        )


@views_bp.get("/recordings/<audio_hash>/panel")
def recording_panel(audio_hash: str) -> flask.Response:
    try:
        date_from = parse_datetime(flask.request.args.get("from"))
        date_to = parse_datetime(flask.request.args.get("to"), end_of_day=True)
        taxon_id = parse_int(flask.request.args.get("taxon"))
        verdict = parse_verdict(flask.request.args.get("verdict"))
        session_id = parse_int(flask.request.args.get("session"))
        site_id = parse_int(flask.request.args.get("site"))
        source_raw = flask.request.args.get("source")
        source = IdSource(source_raw) if source_raw else None
    except ValueError as exc:
        return flask.make_response((str(exc), 400))

    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    filter_qs = flask.request.query_string.decode()

    with OrmSession(engine) as session:
        recordings = filtered_recordings(
            session,
            date_from=date_from,
            date_to=date_to,
            taxon_id=taxon_id,
            verdict=verdict,
            session_id=session_id,
            site_id=site_id,
            source=source,
        )
        neighbors = neighbor_recordings(recordings, audio_hash)
        if neighbors is None:
            html = flask.render_template("_recording_panel.html", found=False)
            return flask.make_response(html)

        previous, following = neighbors
        recording = next(r for r in recordings if r.audio_hash == audio_hash)
        best = current_best_identification(recording)
        taxon = None
        if best is not None and best.taxon_id is not None:
            taxon = session.get(Taxon, best.taxon_id)
        point = decode_point(recording.geom)

        html = flask.render_template(
            "_recording_panel.html",
            found=True,
            recording=recording,
            best=best,
            taxon=taxon,
            previous=previous,
            next=following,
            filter_qs=filter_qs,
            spectrogram_ready=spectrogram_path(media_root, audio_hash).exists(),
            preview_ready=preview_path(media_root, audio_hash).exists(),
        )

    response = flask.make_response(html)
    if point is not None:
        response.headers["HX-Trigger"] = json.dumps(
            {
                "recording-selected": {
                    "hash": recording.audio_hash,
                    "latitude": point[1],
                    "longitude": point[0],
                },
            },
        )
    return response
