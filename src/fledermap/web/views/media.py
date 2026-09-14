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
import math
import os
import tempfile
from collections.abc import Callable, Hashable
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
from fledermap.media.preview import make_preview
from fledermap.media.render_cache import RenderCache
from fledermap.media.spectrogram import (
    FullSpectrogramImage,
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
from fledermap.web.params import parse_bool

media_bp = flask.Blueprint("media", __name__)

logger = logging.getLogger(__name__)

# One cache for the whole `fledermap serve` process (design: media/render_cache.py's own
# module docstring) -- reused across every tile of a recording-detail page load so
# `detail_spectrogram` below computes the shared STFT/palette image once per view instead of
# once per tile.
_spectrogram_image_cache: RenderCache[FullSpectrogramImage] = RenderCache()

# A second, separate cache instance for the TE/HET preview-audio routes below -- deliberately
# not sharing `_spectrogram_image_cache`'s instance (different value type, different size
# needs: cached files here are small, hundreds of KB, not the multi-hundred-MB in-memory arrays
# the spectrogram cache holds, so a more generous `max_size` costs little). `on_evict` deletes
# the cached render's backing temp file -- without this, an evicted entry would leak its file on
# disk forever, since nothing else ever unlinks it once `_serve_cached_render` stops doing so
# after every single response (see that function's docstring for why it stopped).
_preview_render_cache: RenderCache[Path] = RenderCache(
    max_size=8, on_evict=lambda path: path.unlink(missing_ok=True)
)


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
        params.cutoff_hz,
        params.denoise,
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
    """Renders to a throwaway temp file and serves it via `send_file` --
    deliberately not `spectrogram_path`/`oscillogram_path`/`preview_path`
    under the media root: this route is not part of the cached-derived-media
    system (design spec Non-goals), so nothing here is meant to persist.

    `send_file` (not a raw `flask.Response(data, ...)`, the previous approach
    here) matters for more than convenience: it's what gives `_serve_derived`
    above `Accept-Ranges`/`Range`-request support (`conditional=True`, Flask's
    own default) for free. Without it, HET playback -- the only caller that
    streams audio through this path -- broke real seeking: a raw `Response`
    body always returns the full 200 content regardless of an incoming
    `Range` header, and Chrome's `<audio>` element responds to a backward
    seek issued mid-playback against a resource that never signals range
    support by silently resetting `currentTime` to 0 instead of actually
    seeking there (Janna, 2026-09-04, live use -- confirmed live: the TE
    preview, served via `send_file` all along through `_serve_derived`, never
    showed this with the exact same click sequence, and HET, the only
    `_serve_temp_render` audio caller, always did). The temp file has to
    still exist when `send_file` actually reads it to build a `Range`
    response, so cleanup is deferred to `after_this_request` rather than the
    `finally` block this replaced, which deleted the file before the
    response was ever sent."""
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        make(tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    @flask.after_this_request
    def _cleanup(response: flask.Response) -> flask.Response:
        tmp_path.unlink(missing_ok=True)
        return response

    return flask.send_file(tmp_path, mimetype=mimetype, conditional=True)


def _serve_cached_render(
    cache_key: Hashable,
    make: Callable[[Path], None],
    *,
    suffix: str,
    mimetype: str,
) -> ResponseReturnValue:
    """Like `_serve_temp_render`, but for the TE/HET preview-audio routes below, which need a
    DIFFERENT contract: `_serve_temp_render` deletes its temp file after every single response,
    which is correct for the tile routes (each tile request is genuinely independent) but was
    the root cause of a real playback bug for audio (design spec
    docs/superpowers/specs/2026-09-14-fledermap-audio-denoise-design.md's follow-up fix). A
    browser doesn't fetch preview audio in one request -- it buffers ahead and seeks via several
    `Range` sub-requests over the course of one playback session, and `ffmpeg`'s Ogg muxer picks
    a new random stream serial number on every invocation: two independent renders of identical
    audio are never byte-identical. Re-rendering fresh (and deleting) on every request meant a
    browser's second Range request could land on a DIFFERENT re-render than its first, splicing
    together fragments of two logically different Ogg streams -- silently corrupting playback,
    observed live as it stopping partway through, never at the file's real end, at a different
    point each time depending on when the next request happened to fire.

    The fix: render ONCE per `cache_key` via `_preview_render_cache`
    (`media/render_cache.py`'s sliding-TTL, `on_evict`-cleaned-up cache) and keep serving that
    SAME file for as long as it stays cached -- every request within one active playback session
    lands on identical bytes. `send_file(path, conditional=True)` against that stable file also
    gets a real ETag/Last-Modified for free (Werkzeug derives both from the file's own stat
    info), which is a second, independent line of defense: if the cache entry does eventually
    expire and a later request re-renders (a genuinely new file, new mtime, new ETag), a
    browser's `If-Range` validation correctly sees the mismatch and falls back to a full 200
    instead of continuing to splice against stale Range offsets."""
    path = _preview_render_cache.get_or_compute(
        cache_key, lambda: _render_to_temp_file(make, suffix)
    )
    return flask.send_file(path, mimetype=mimetype, conditional=True)


def _render_to_temp_file(make: Callable[[Path], None], suffix: str) -> Path:
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        make(tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


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
    denoise = parse_bool(flask.request.args.get("denoise"))
    spectrogram_params = dataclasses.replace(spectrogram_params, denoise=denoise)
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
    denoise = parse_bool(flask.request.args.get("denoise"))
    oscillogram_params = dataclasses.replace(oscillogram_params, denoise=denoise)
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
    if not math.isfinite(freq_hz):
        flask.abort(400)

    denoise = parse_bool(flask.request.args.get("denoise"))
    wav_path = _resolve_wav_path_or_404(audio_hash)
    cache_key = ("het", str(wav_path), wav_path.stat().st_mtime_ns, freq_hz, denoise)
    try:
        return _serve_cached_render(
            cache_key,
            lambda out: render_heterodyne_preview(
                wav_path,
                out,
                tune_freq_hz=freq_hz,
                denoise=denoise,
            ),
            suffix=".opus",
            mimetype="audio/ogg",
        )
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)


@media_bp.get("/recordings/<audio_hash>/detail-preview.opus")
def detail_preview(audio_hash: str) -> ResponseReturnValue:
    """Details-page-only, live-rendered TE preview -- deliberately NOT the
    shared, job-queued, disk-cached `preview` route above (that route has
    no per-render params dimension; see design spec's TE/HET section for
    why a new caching dimension there would be real new infrastructure for
    a feature most recordings will never have toggled). The details page
    keeps using the cached `preview` route when denoise is off; this route
    is only ever hit once a user actually turns the toggle on.

    Served via `_serve_cached_render`'s short-lived, sliding-TTL cache, not
    `_serve_temp_render` -- see that function's docstring for why a
    request-scoped render broke real playback here specifically."""
    denoise = parse_bool(flask.request.args.get("denoise"))
    wav_path = _resolve_wav_path_or_404(audio_hash)
    cache_key = ("te", str(wav_path), wav_path.stat().st_mtime_ns, denoise)
    return _serve_cached_render(
        cache_key,
        lambda out: make_preview(wav_path, out, denoise=denoise),
        suffix=".opus",
        mimetype="audio/ogg",
    )


@media_bp.get("/recordings/<audio_hash>/peak-frequency")
def peak_frequency(audio_hash: str) -> ResponseReturnValue:
    wav_path = _resolve_wav_path_or_404(audio_hash)
    try:
        peak_hz = compute_peak_frequency_hz(wav_path)
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)
    return flask.jsonify({"peak_frequency_hz": peak_hz})
