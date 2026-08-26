"""Serves derived media (spectrograms, audio previews) written under
`Config.media_root` by the jobs in `jobs/tasks.py` (design spec section 8;
parent spec section 9 names this route but no phase had built it yet).

Every route resolves `audio_hash` against the `Recording` table BEFORE
touching the filesystem -- not just to 404 for an unknown recording, but
because it's the path-traversal guard: `media/paths.py`'s helpers join
`audio_hash` directly into a filesystem path, and a hash that doesn't match
any real `Recording` row never reaches them.
"""

from __future__ import annotations

import flask
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.paths import preview_path, spectrogram_path
from fledermap.store.models import Recording

media_bp = flask.Blueprint("media", __name__)


def _known_hash(session: OrmSession, audio_hash: str) -> bool:
    return (
        session.scalars(
            select(Recording.id).where(Recording.audio_hash == audio_hash),
        ).first()
        is not None
    )


@media_bp.get("/media/<audio_hash>/spectrogram.webp")
def spectrogram(audio_hash: str) -> ResponseReturnValue:
    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    with OrmSession(engine) as session:
        if not _known_hash(session, audio_hash):
            flask.abort(404)
    path = spectrogram_path(media_root, audio_hash)
    if not path.exists():
        flask.abort(404)
    return flask.send_file(path, mimetype="image/webp")


@media_bp.get("/media/<audio_hash>/preview.opus")
def preview(audio_hash: str) -> ResponseReturnValue:
    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    with OrmSession(engine) as session:
        if not _known_hash(session, audio_hash):
            flask.abort(404)
    path = preview_path(media_root, audio_hash)
    if not path.exists():
        flask.abort(404)
    return flask.send_file(path, mimetype="audio/ogg")
