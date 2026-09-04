"""The standalone recording details page (design spec
2026-09-01-fledermap-recording-details-page-design.md, section 3) -- a full
page, not an HTMX drawer fragment, matching `sessions.py`'s own precedent
for a detail view that deserves the whole screen rather than the drawer's
small, drag-resized panel."""

from __future__ import annotations

import flask
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.preview import TIME_EXPANSION_FACTOR
from fledermap.services.current_best import current_best_identification
from fledermap.services.recording_detail import (
    DETAIL_PX_PER_KHZ,
    DETAIL_PX_PER_MS,
    detail_params,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site, Taxon
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.params import fallback_site_label

recording_detail_bp = flask.Blueprint(
    "recording_detail",
    __name__,
    template_folder="../templates",
)

_DEFAULT_BACK_LINK = ("Back to map", "/")

# ASCII tab/CR/LF are stripped by the WHATWG URL parser before it looks at a
# URL's structure, so a raw one of these -- indistinguishable from a normal
# path character to a plain `str` check -- must be rejected before it can
# hide a "//" (or a backslash, see below) from `_is_safe_relative_path`.
_URL_STRIPPED_CHARS = str.maketrans({"\t": None, "\r": None, "\n": None})


def _is_safe_relative_path(value: str) -> bool:
    """A same-origin relative path is safe to redirect to; anything a
    browser's URL parser could turn into a protocol-relative (or absolute)
    URL is not. Browsers normalize backslashes to forward slashes and strip
    tab/CR/LF while parsing an http(s) URL (WHATWG URL spec), so both must
    be normalized here too -- a literal `startswith("//")` check alone is
    bypassed by `/\\evil.example` or a tab hidden between two slashes, both
    of which a browser still resolves to `//evil.example`."""
    normalized = value.translate(_URL_STRIPPED_CHARS).replace("\\", "/")
    return normalized.startswith("/") and not normalized.startswith("//")


def _resolve_back_link(return_to: str | None) -> tuple[str, str]:
    """Turn a caller-supplied `return_to` (a page's own path+query, e.g. the
    map drawer's `/?{filter_qs}`) into a (label, url) pair for the details
    page's back link -- backlog note "Back to map": different text per
    origin, falling back to the map when the origin is missing or not one we
    recognise. Anything not a safe same-origin relative path (see
    `_is_safe_relative_path`) is rejected rather than sending the user
    off-site."""
    if return_to is None or not _is_safe_relative_path(return_to):
        return _DEFAULT_BACK_LINK
    if return_to == "/" or return_to.startswith("/?"):
        return ("Back to map", return_to)
    if return_to == "/sessions" or return_to.startswith("/sessions/"):
        return ("Back to sessions", return_to)
    return ("Back", return_to)


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

        recording_session = (
            session.get(AnnotationSession, recording.session_id)
            if recording.session_id
            else None
        )

        params = None
        if recording.duration_s is not None and recording.samplerate_hz is not None:
            params = detail_params(recording.duration_s, recording.samplerate_hz)

        back_label, back_url = _resolve_back_link(flask.request.args.get("return_to"))

        html = flask.render_template(
            "recording_details.html",
            recording=recording,
            best=best,
            taxon=taxon,
            site=site,
            site_label=site_label,
            recording_session=recording_session,
            duration_s=recording.duration_s,
            params=params,
            px_per_ms=DETAIL_PX_PER_MS,
            px_per_khz=DETAIL_PX_PER_KHZ,
            time_expansion_factor=TIME_EXPANSION_FACTOR,
            back_label=back_label,
            back_url=back_url,
        )
    return flask.make_response(html)
