"""Serves derived media (spectrograms, oscillograms, audio previews) written
under `Config.media_root` by the jobs in `jobs/tasks.py` (design spec
section 8; parent spec section 9 names this route but no phase had built it
yet).

Every route resolves `audio_hash` against the `Recording` table BEFORE
touching the filesystem -- not just to 404 for an unknown recording, but
because it's the path-traversal guard: `media/paths.py`'s helpers join
`audio_hash` directly into a filesystem path, and a hash that doesn't match
any real `Recording` row never reaches them.
"""

from __future__ import annotations

from pathlib import Path

import flask
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.store.models import Recording

media_bp = flask.Blueprint("media", __name__)


def _known_hash(session: OrmSession, audio_hash: str) -> bool:
    return (
        session.scalars(
            select(Recording.id).where(Recording.audio_hash == audio_hash),
        ).first()
        is not None
    )


def _serve_derived(audio_hash: str, path: Path, mimetype: str) -> ResponseReturnValue:
    """Shared by every route below: check the hash is real (path-traversal
    guard, see module docstring), then serve the file if it's been rendered
    yet. A third route landing here (the oscillogram) is what tipped this
    from "two near-identical bodies" (flagged as a parked minor finding
    during Phase 5a's drawer work) into "worth the helper" -- exactly the
    threshold that finding's own resolution named."""
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        if not _known_hash(session, audio_hash):
            flask.abort(404)
    if not path.exists():
        flask.abort(404)
    return flask.send_file(path, mimetype=mimetype)


@media_bp.get("/media/<audio_hash>/spectrogram.webp")
def spectrogram(audio_hash: str) -> ResponseReturnValue:
    media_root = flask.current_app.config["MEDIA_ROOT"]
    return _serve_derived(
        audio_hash,
        spectrogram_path(media_root, audio_hash),
        "image/webp",
    )


@media_bp.get("/media/<audio_hash>/oscillogram.webp")
def oscillogram(audio_hash: str) -> ResponseReturnValue:
    media_root = flask.current_app.config["MEDIA_ROOT"]
    return _serve_derived(
        audio_hash,
        oscillogram_path(media_root, audio_hash),
        "image/webp",
    )


@media_bp.get("/media/<audio_hash>/preview.opus")
def preview(audio_hash: str) -> ResponseReturnValue:
    media_root = flask.current_app.config["MEDIA_ROOT"]
    return _serve_derived(
        audio_hash,
        preview_path(media_root, audio_hash),
        "audio/ogg",
    )
