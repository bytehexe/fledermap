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
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import flask
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.heterodyne import (
    compute_peak_frequency_hz,
    render_heterodyne_preview,
)
from fledermap.media.oscillogram import OscillogramParams, render_oscillogram
from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.media.render_cache import SpectrogramImageCache
from fledermap.media.spectrogram import (
    SpectrogramParams,
    render_full_spectrogram_image,
    render_spectrogram,
)
from fledermap.media.wav_pcm import UnreadableWavError
from fledermap.services.media import resolve_recording, resolve_wav_path
from fledermap.services.recording_detail import (
    DETAIL_PX_PER_MS,
    DetailTile,
    detail_params,
)
from fledermap.store.models import Recording

media_bp = flask.Blueprint("media", __name__)

logger = logging.getLogger(__name__)

# One cache for the whole `fledermap serve` process (design: media/render_cache.py's own
# module docstring) -- reused across every tile of a recording-detail page load so
# `detail_spectrogram` below computes the shared STFT/palette image once per view instead of
# once per tile.
_spectrogram_image_cache = SpectrogramImageCache()


def _spectrogram_image_cache_key(
    wav_path: Path,
    params: SpectrogramParams,
) -> tuple[object, ...]:
    """Everything that affects `render_full_spectrogram_image`'s output -- deliberately
    excluding `width_px`/`height_px` (those only govern the final per-tile resize, not the
    cached shared image; including them would make every tile of one recording a cache miss
    against every other tile, defeating the whole point). `wav_path`'s mtime guards against the
    unlikely case of the same path later holding different content -- `resolve_wav_path`
    already returns a different path after a real re-ID rename (D8), so this is a second,
    cheap layer of safety, not the primary invalidation mechanism."""
    return (
        str(wav_path),
        wav_path.stat().st_mtime_ns,
        params.window_ms,
        params.overlap,
        params.max_freq_hz,
        params.dynamic_range_db,
        params.palette,
    )


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


def _serve_temp_render(
    make: Callable[[Path], None],
    *,
    suffix: str,
    mimetype: str,
) -> ResponseReturnValue:
    """Renders to a throwaway temp file and streams the bytes back --
    deliberately not `spectrogram_path`/`oscillogram_path`/`preview_path`
    under the media root: this route is not part of the cached-derived-media
    system (design spec Non-goals), so nothing here is meant to persist."""
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        make(tmp_path)
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return flask.Response(data, mimetype=mimetype)


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
    try:
        # Render-cost optimization (v1 backlog "render-cost optimization for tiled long
        # recordings"): the STFT/palette image is identical across every tile of this
        # recording, so it's computed once and reused for the rest of the page's tile
        # requests instead of once per tile (media/render_cache.py).
        cache_key = _spectrogram_image_cache_key(wav_path, spectrogram_params)
        full_image = _spectrogram_image_cache.get_or_compute(
            cache_key,
            lambda: render_full_spectrogram_image(wav_path, spectrogram_params),
        )
        return _serve_temp_render(
            lambda out: render_spectrogram(
                wav_path,
                out,
                params=tile_params,
                time_range_s=time_range_s,
                full_image=full_image,
            ),
            suffix=".webp",
            mimetype="image/webp",
        )
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)


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
    try:
        return _serve_temp_render(
            lambda out: render_oscillogram(
                wav_path,
                out,
                params=tile_params,
                time_range_s=time_range_s,
            ),
            suffix=".webp",
            mimetype="image/webp",
        )
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)


def _resolve_wav_path_or_404(audio_hash: str) -> Path:
    """Shared by the two routes below -- resolves straight via
    `resolve_recording`/`resolve_wav_path`, NOT `_detail_tile_context`: that
    helper also requires `duration_s`/`samplerate_hz`, a real requirement
    for computing tile boundaries that doesn't apply here (HET plays the
    whole file, nothing is tiled). Requiring it anyway would incorrectly
    block HET playback on metadata it doesn't actually need (design spec
    section 2)."""
    engine = flask.current_app.config["ENGINE"]
    archive_roots = flask.current_app.config["ARCHIVE_ROOTS"]
    with OrmSession(engine) as session:
        try:
            recording = resolve_recording(session, audio_hash)
        except (NoResultFound, FileNotFoundError):
            flask.abort(404)
        try:
            wav_path = resolve_wav_path(archive_roots, recording)
        except FileNotFoundError:
            flask.abort(404)
    if not wav_path.exists():
        flask.abort(404)
    return wav_path


@media_bp.get("/recordings/<audio_hash>/het-preview.opus")
def het_preview(audio_hash: str) -> ResponseReturnValue:
    freq_hz_raw = flask.request.args.get("freq_hz")
    if freq_hz_raw is None:
        flask.abort(400)
    try:
        freq_hz = float(freq_hz_raw)
    except ValueError:
        flask.abort(400)

    wav_path = _resolve_wav_path_or_404(audio_hash)
    try:
        return _serve_temp_render(
            lambda out: render_heterodyne_preview(wav_path, out, tune_freq_hz=freq_hz),
            suffix=".opus",
            mimetype="audio/ogg",
        )
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)


@media_bp.get("/recordings/<audio_hash>/peak-frequency")
def peak_frequency(audio_hash: str) -> ResponseReturnValue:
    wav_path = _resolve_wav_path_or_404(audio_hash)
    try:
        peak_hz = compute_peak_frequency_hz(wav_path)
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)
    return flask.jsonify({"peak_frequency_hz": peak_hz})
