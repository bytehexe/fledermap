"""The standalone recording details page (design spec
2026-09-01-fledermap-recording-details-page-design.md, section 3) -- a full
page, not an HTMX drawer fragment, matching `sessions.py`'s own precedent
for a detail view that deserves the whole screen rather than the drawer's
small, drag-resized panel."""

from __future__ import annotations

import flask
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.services.recording_detail import (
    DETAIL_PX_PER_KHZ,
    DETAIL_PX_PER_MS,
    detail_params,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site, Taxon
from fledermap.web.params import fallback_site_label

recording_detail_bp = flask.Blueprint(
    "recording_detail",
    __name__,
    template_folder="../templates",
)


@recording_detail_bp.get("/recordings/<audio_hash>")
def recording_details_page(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            flask.abort(404)

        best = current_best_identification(recording)
        taxon = None
        if best is not None and best.taxon_id is not None:
            taxon = session.get(Taxon, best.taxon_id)

        site = session.get(Site, recording.site_id) if recording.site_id else None
        site_label = None
        if site is not None:
            site_label = (
                site.name
                if site.name
                else fallback_site_label(decode_point(site.centroid))
            )

        params = None
        if recording.duration_s is not None and recording.samplerate_hz is not None:
            params = detail_params(recording.duration_s, recording.samplerate_hz)

        html = flask.render_template(
            "recording_details.html",
            recording=recording,
            best=best,
            taxon=taxon,
            site=site,
            site_label=site_label,
            duration_s=recording.duration_s,
            params=params,
            px_per_ms=DETAIL_PX_PER_MS,
            px_per_khz=DETAIL_PX_PER_KHZ,
        )
    return flask.make_response(html)
