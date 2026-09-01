"""GeoJSON endpoints for the map (design spec section 6)."""

from __future__ import annotations

import flask
from flask.typing import ResponseReturnValue
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.services.current_best import current_best_identification
from fledermap.services.map_query import (
    MAX_FEATURES,
    filtered_recordings,
    filtered_sites,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site, Taxon
from fledermap.web.params import (
    fallback_site_label,
    parse_bbox,
    parse_bool,
    parse_datetime,
    parse_int,
    parse_verdict,
)

api_bp = flask.Blueprint("api", __name__, url_prefix="/api")


def _recording_feature(recording: Recording, session: OrmSession) -> dict[str, object]:
    point = decode_point(recording.geom)
    best = current_best_identification(recording)
    taxon_name = None
    if best is not None and best.taxon_id is not None:
        taxon = session.get(Taxon, best.taxon_id)
        if taxon is not None:
            taxon_name = taxon.scientific_name
    return {
        "type": "Feature",
        "geometry": (
            {"type": "Point", "coordinates": [point[0], point[1]]}
            if point is not None
            else None
        ),
        "properties": {
            "audio_hash": recording.audio_hash,
            "recorded_at": recording.recorded_at.isoformat(),
            "taxon_id": best.taxon_id if best is not None else None,
            "taxon_name": taxon_name,
            "verdict": best.verdict.value if best is not None else None,
            "source": best.source.value if best is not None else None,
        },
    }


def _site_feature(site: Site) -> dict[str, object]:
    point = decode_point(site.centroid)
    return {
        "type": "Feature",
        "geometry": (
            {"type": "Point", "coordinates": [point[0], point[1]]}
            if point is not None
            else None
        ),
        "properties": {
            "id": site.id,
            "name": site.name if site.name else fallback_site_label(point),
            "radius_m": site.radius_m,
            "recording_count": site.recording_count,
        },
    }


@api_bp.get("/recordings.geojson")
def recordings_geojson() -> ResponseReturnValue:
    try:
        bbox = parse_bbox(flask.request.args.get("bbox"))
        source_raw = flask.request.args.get("source")
        source = IdSource(source_raw) if source_raw else None
        date_from = parse_datetime(flask.request.args.get("from"))
        date_to = parse_datetime(flask.request.args.get("to"), end_of_day=True)
        taxon_id = parse_int(flask.request.args.get("taxon"))
        taxon_exclude = parse_bool(flask.request.args.get("taxon_exclude"))
        verdict = parse_verdict(flask.request.args.get("verdict"))
        session_id = parse_int(flask.request.args.get("session"))
        site_id = parse_int(flask.request.args.get("site"))
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recordings = filtered_recordings(
            session,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            taxon_id=taxon_id,
            taxon_exclude=taxon_exclude,
            verdict=verdict,
            session_id=session_id,
            site_id=site_id,
            source=source,
        )
        truncated = len(recordings) > MAX_FEATURES
        features = [_recording_feature(r, session) for r in recordings[:MAX_FEATURES]]

    return flask.jsonify(
        {"type": "FeatureCollection", "features": features, "truncated": truncated},
    )


@api_bp.get("/sites.geojson")
def sites_geojson() -> ResponseReturnValue:
    try:
        bbox = parse_bbox(flask.request.args.get("bbox"))
        date_from = parse_datetime(flask.request.args.get("from"))
        date_to = parse_datetime(flask.request.args.get("to"), end_of_day=True)
        site_id = parse_int(flask.request.args.get("site"))
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        sites = filtered_sites(
            session,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            site_id=site_id,
        )
        truncated = len(sites) > MAX_FEATURES
        features = [_site_feature(s) for s in sites[:MAX_FEATURES]]

    return flask.jsonify(
        {"type": "FeatureCollection", "features": features, "truncated": truncated},
    )
