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

import dataclasses
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import flask
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.oscillogram import OscillogramParams, render_oscillogram
from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.media.spectrogram import SpectrogramParams, render_spectrogram
from fledermap.services.media import resolve_recording, resolve_wav_path
from fledermap.services.recording_detail import (
    DETAIL_PX_PER_MS,
    DetailTile,
    detail_params,
)
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


def _detail_tile_context(
    audio_hash: str,
    tile_index: int,
) -> tuple[Path, SpectrogramParams, OscillogramParams, DetailTile] | None:
    """Resolves `audio_hash` and `tile_index` to (wav_path, spectrogram_params,
    oscillogram_params, tile) for the two detail-render routes below, or None for any of: unknown
    recording, no source file (`missing_since` set OR the file simply isn't on disk -- design
    spec section 2 step 2's "missing file" case covers both, only the first of which Task 3
    originally handled), missing duration/samplerate metadata, or an out-of-range `tile_index`."""
    engine = flask.current_app.config["ENGINE"]
    archive_roots = flask.current_app.config["ARCHIVE_ROOTS"]
    with OrmSession(engine) as session:
        try:
            recording = resolve_recording(session, audio_hash)
        except (NoResultFound, FileNotFoundError):
            return None
        if recording.duration_s is None or recording.samplerate_hz is None:
            return None
        try:
            wav_path = resolve_wav_path(archive_roots, recording)
        except FileNotFoundError:
            return None
        if not wav_path.exists():
            return None
        params = detail_params(recording.duration_s, recording.samplerate_hz)
    if tile_index < 0 or tile_index >= len(params.tiles):
        return None
    tile = params.tiles[tile_index]
    return wav_path, params.spectrogram, params.oscillogram, tile


def _serve_temp_render(make: Callable[[Path], None]) -> ResponseReturnValue:
    """Renders to a throwaway temp file and streams the bytes back --
    deliberately not `spectrogram_path`/`oscillogram_path` under the media
    root: this route is not part of the cached-derived-media system (design
    spec Non-goals), so nothing here is meant to persist."""
    fd, tmp_name = tempfile.mkstemp(suffix=".webp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        make(tmp_path)
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return flask.Response(data, mimetype="image/webp")


@media_bp.get("/recordings/<audio_hash>/detail-spectrogram/<int:tile_index>.webp")
def detail_spectrogram(audio_hash: str, tile_index: int) -> ResponseReturnValue:
    context = _detail_tile_context(audio_hash, tile_index)
    if context is None:
        flask.abort(404)
    wav_path, spectrogram_params, _oscillogram_params, tile = context
    time_range_s = (
        tile.start_px / DETAIL_PX_PER_MS / 1000,
        (tile.start_px + tile.width_px) / DETAIL_PX_PER_MS / 1000,
    )
    tile_params = dataclasses.replace(spectrogram_params, width_px=tile.width_px)
    return _serve_temp_render(
        lambda out: render_spectrogram(
            wav_path,
            out,
            params=tile_params,
            time_range_s=time_range_s,
        ),
    )


@media_bp.get("/recordings/<audio_hash>/detail-oscillogram/<int:tile_index>.webp")
def detail_oscillogram(audio_hash: str, tile_index: int) -> ResponseReturnValue:
    context = _detail_tile_context(audio_hash, tile_index)
    if context is None:
        flask.abort(404)
    wav_path, _spectrogram_params, oscillogram_params, tile = context
    time_range_s = (
        tile.start_px / DETAIL_PX_PER_MS / 1000,
        (tile.start_px + tile.width_px) / DETAIL_PX_PER_MS / 1000,
    )
    tile_params = dataclasses.replace(oscillogram_params, width_px=tile.width_px)
    return _serve_temp_render(
        lambda out: render_oscillogram(
            wav_path,
            out,
            params=tile_params,
            time_range_s=time_range_s,
        ),
    )
