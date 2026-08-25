"""GeoJSON endpoints for the map (design spec section 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import flask
from flask.typing import ResponseReturnValue
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.services.map_query import (
    MAX_FEATURES,
    BBox,
    filtered_recordings,
    filtered_sites,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site

api_bp = flask.Blueprint("api", __name__, url_prefix="/api")


def _parse_bbox(raw: str | None) -> BBox | None:
    if raw is None:
        return None
    parts = raw.split(",")
    msg = "bbox must be 4 comma-separated numbers: min_lon,min_lat,max_lon,max_lat"
    if len(parts) != 4:
        raise ValueError(msg)
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError:
        raise ValueError(msg) from None
    return (min_lon, min_lat, max_lon, max_lat)


def _parse_datetime(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def _parse_verdict(raw: str | None) -> Verdict | Literal["all"] | None:
    if raw is None:
        return None
    if raw == "all":
        return "all"
    return Verdict(raw)


def _parse_int(raw: str | None) -> int | None:
    return int(raw) if raw else None


def _fallback_site_label(point: tuple[float, float] | None) -> str:
    """P4-1: Site.name is unpopulated until poiidx naming ships as its own
    task -- fall back to a rounded-coordinate label rather than block this
    phase on that unrelated integration."""
    if point is None:
        return "Site"
    lon, lat = point
    return f"{lat:.4f}, {lon:.4f}"


def _recording_feature(recording: Recording) -> dict[str, object]:
    point = decode_point(recording.geom)
    best = current_best_identification(recording)
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
            "name": site.name if site.name else _fallback_site_label(point),
            "radius_m": site.radius_m,
            "recording_count": site.recording_count,
        },
    }


@api_bp.get("/recordings.geojson")
def recordings_geojson() -> ResponseReturnValue:
    try:
        bbox = _parse_bbox(flask.request.args.get("bbox"))
        source_raw = flask.request.args.get("source")
        source = IdSource(source_raw) if source_raw else None
        date_from = _parse_datetime(flask.request.args.get("from"))
        date_to = _parse_datetime(flask.request.args.get("to"))
        taxon_id = _parse_int(flask.request.args.get("taxon"))
        verdict = _parse_verdict(flask.request.args.get("verdict"))
        session_id = _parse_int(flask.request.args.get("session"))
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
            verdict=verdict,
            session_id=session_id,
            source=source,
        )
        truncated = len(recordings) > MAX_FEATURES
        features = [_recording_feature(r) for r in recordings[:MAX_FEATURES]]

    return flask.jsonify(
        {"type": "FeatureCollection", "features": features, "truncated": truncated},
    )


@api_bp.get("/sites.geojson")
def sites_geojson() -> ResponseReturnValue:
    try:
        bbox = _parse_bbox(flask.request.args.get("bbox"))
        date_from = _parse_datetime(flask.request.args.get("from"))
        date_to = _parse_datetime(flask.request.args.get("to"))
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        sites = filtered_sites(
            session,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
        )
        truncated = len(sites) > MAX_FEATURES
        features = [_site_feature(s) for s in sites[:MAX_FEATURES]]

    return flask.jsonify(
        {"type": "FeatureCollection", "features": features, "truncated": truncated},
    )
