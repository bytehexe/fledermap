"""Spectrogram rendering. Pure: reads a WAV file, writes a WebP image. No DB,
no queue awareness (design spec §3) -- `jobs/tasks.py` is the only caller.

Written fresh against scipy/numpy/Pillow rather than ported from batogram
(design spec §2, decision P3-1): batogram is a Tkinter GUI application with no
stable, separable library API, not a clean porting target the way
mkmapdiary's LocalProjection/GeoCluster were in Phase 2.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image
from scipy import signal

from fledermap.media.denoise import (
    DEFAULT_CUTOFF_HZ,
    denoised_samples,
    subtract_background_noise,
)
from fledermap.media.wav_pcm import read_pcm


@dataclass(frozen=True)
class SpectrogramParams:
    """Every field that affects rendered output. `params_hash` is the on-disk
    filename's `<params>` component (design spec §8/parent spec §8) --
    changing any field here invalidates existing renders without touching
    `audio_hash`, so a settings change never requires a migration."""

    window_ms: float = 3.0
    overlap: float = 0.5
    # 128 kHz covers the practical bat-call range (roughly 9-212 kHz across
    # this project's EU species list, docs/references.md) without wasting
    # resolution on near-silent bins above it. This happens to equal the
    # Nyquist frequency of the bundled EMT's 256 kHz sample rate -- named
    # explicitly so nobody mistakes that coincidence for the reason.
    # `render_spectrogram` clamps to the SOURCE recording's own Nyquist limit
    # at render time regardless of this value (design spec §4) -- this field
    # is a ceiling, not a promise every recording reaches it.
    max_freq_hz: float = 128_000.0
    width_px: int = 1024
    height_px: int = 512
    # dB below the recording's own loudest bin at which the palette bottoms
    # out at its floor colour. 80 dB is the conventional audio-spectrogram
    # window (Audacity, batogram): wide enough to show a call's full shape,
    # narrow enough that the noise floor still reads as visibly dark rather
    # than the near-black-vs-near-black smear log-magnitude min-max
    # normalisation produced (see `_normalise`'s docstring).
    dynamic_range_db: float = 80.0
    # Named, not a raw function, so it's a plain hashable value in
    # `params_hash` (a function reference is not a stable hash input across
    # process restarts) and so a future second palette is just another
    # string here, not a schema change.
    palette: str = "black_blue_rainbow_red"
    # Low-frequency noise-band boundary (design spec
    # docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md): always excluded
    # from the color-normalization peak below, and used as the highpass cutoff when `denoise`
    # is on. A real dataclass field (not a bare module constant) so `params_hash` invalidates
    # existing cached renders if this value is ever revisited.
    cutoff_hz: float = DEFAULT_CUTOFF_HZ
    denoise: bool = False
    # Bump whenever the pixel <-> time/frequency geometry of the render changes, so the on-disk
    # cache (named by `params_hash`) re-renders instead of serving old pixels under new axes.
    # 1: the STFT's first/last bin and column were stretched to the image edges, so a bin sat up
    #    to half a bin (0.33 kHz at the detail page's 1.5 ms window) and a column up to ~0.6 ms
    #    away from where the axes place its frequency/time.
    # 2: bin and column CENTRES sit exactly on the axes (`_resample_onto_axes`).
    geometry_version: int = 2

    @property
    def params_hash(self) -> str:
        payload = "|".join(str(getattr(self, f.name)) for f in fields(self))
        return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


# The one settings instance the whole project renders and names files with.
# Kept as a single shared object so `params_hash` cannot diverge between the
# code that WRITES a spectrogram and the code that looks for one on disk.
DEFAULT_SPECTROGRAM_PARAMS = SpectrogramParams()

# black -> blue -> cyan -> green -> yellow -> red. Deliberately starts at
# black (the floor, i.e. "at or below the dynamic-range cutoff") rather than
# a dark blue like matplotlib's "jet": the floor colour needs to read as
# "nothing here", and black reads that way more clearly against a page
# background than a dark colour does. Hand-rolled rather than pulling in
# matplotlib (a heavy dependency this project otherwise has no use for) --
# a piecewise-linear interpolation over a handful of anchor colours is a
# five-line numpy function.
_PALETTES: dict[str, tuple[tuple[float, tuple[int, int, int]], ...]] = {
    "black_blue_rainbow_red": (
        (0.00, (0, 0, 0)),
        (0.25, (0, 0, 180)),
        (0.50, (0, 180, 180)),
        (0.65, (0, 200, 0)),
        (0.80, (255, 230, 0)),
        (1.00, (255, 0, 0)),
    ),
}


def _palette_lut(name: str) -> np.ndarray:
    """A 256x3 uint8 lookup table for `name`, built once per render (cheap:
    256 rows) rather than cached, since palette changes are rare and this
    keeps the function free of module-level mutable state."""
    stops = _PALETTES[name]
    positions = np.array([p for p, _ in stops])
    channels = np.array([c for _, c in stops])
    indices = np.linspace(0.0, 1.0, 256)
    lut = np.stack(
        [np.interp(indices, positions, channels[:, ch]) for ch in range(3)],
        axis=-1,
    )
    return lut.astype(np.uint8)


def effective_max_freq_hz(samplerate_hz: float, params: SpectrogramParams) -> float:
    """The actual top of the rendered frequency axis for a recording at
    `samplerate_hz`: `params.max_freq_hz` is a ceiling, clamped to this
    recording's own Nyquist limit (design spec §4). Exposed as its own
    function -- not just inlined in `render_spectrogram` -- because
    `web/views/map.py` needs this exact number too, to label the frequency
    axis correctly; a second, hand-copied formula there would drift from
    this one silently the same way `media/paths.py`'s docstring warns about
    for writer/reader path formulas."""
    return min(params.max_freq_hz, samplerate_hz / 2)


def stft_hop_samples(samplerate_hz: float, window_ms: float, overlap: float) -> int:
    """The real STFT hop length in samples for these params -- the same `nperseg`/`noverlap`
    formula `render_spectrogram` uses internally (minus its `len(samples)` floor, which only
    matters for a signal shorter than one window and isn't knowable ahead of a real recording).
    Exposed as its own function -- not just inlined in `render_spectrogram` -- because
    `services/recording_detail.py` needs this exact number too, to derive its locked time scale
    at the exact point where one real STFT column maps to one display pixel; a second, hand-copied
    formula there would drift from this one silently the same way `effective_max_freq_hz`'s
    docstring above warns about for `web/views/map.py`."""
    nperseg = max(int(samplerate_hz * window_ms / 1000), 8)
    noverlap = int(nperseg * overlap)
    return nperseg - noverlap


class FullSpectrogramImage(NamedTuple):
    """`render_full_spectrogram_image`'s result: the rendered image plus what
    `render_spectrogram` needs to place it on real time/frequency axes -- the STFT's own `times`
    (column centres, seconds) and `freqs` (bin centres, Hz, ascending from 0), the recording's
    `duration_s`, and the top of the frequency axis (`max_freq_hz`, already clamped to Nyquist).
    A plain typed tuple rather than attributes stashed on the `Image.Image` -- keeps both call
    sites free of `# type: ignore`s for attributes PIL's own stubs don't know about."""

    image: Image.Image
    times: np.ndarray
    freqs: np.ndarray
    duration_s: float
    max_freq_hz: float


def render_full_spectrogram_image(
    wav_path: Path,
    params: SpectrogramParams,
    *,
    samples: np.ndarray | None = None,
    samplerate: int | None = None,
) -> FullSpectrogramImage:
    """The expensive, tile-independent part of `render_spectrogram`: STFT via
    `scipy.signal.spectrogram`, power converted to dB relative to the recording's own peak,
    clipped to `params.dynamic_range_db` and mapped through `params.palette`'s colour lookup
    table -- at the STFT's own native time/frequency resolution, NOT `params.width_px`/
    `height_px` (those only matter for the final per-tile resize `render_spectrogram` does with
    this image's help; two `SpectrogramParams` differing only in those two fields produce
    identically-sized images here).

    Extracted out of `render_spectrogram` (render-cost optimization, v1 backlog "render-cost
    optimization for tiled long recordings") so a caller rendering many tiles of the SAME
    recording -- the standalone recording-detail page's `detail_spectrogram` route -- can
    compute this once and reuse it via `render_spectrogram`'s `full_image` parameter, instead of
    redoing the whole STFT (measured ~25% of one tile's render time on a real 30s field
    recording) on every single tile request. `render_spectrogram` itself stays the single
    source of truth for every other step (per-tile resize, atomic write) and every existing
    caller (the drawer/overview's cached single-image render) is unaffected -- this function is
    purely an extraction, not a behavior change.

    `samples`/`samplerate`, if given, are used AS-IS -- `wav_path` is not read and
    `params.denoise` is not applied again -- for a caller that already read and (if wanted)
    denoised this recording once (`web/views/media.py`'s denoised-samples cache, shared across
    a page view's spectrogram AND every oscillogram tile request, v1 backlog "render-cost/memory
    regression from audio-domain denoising, uncached"). Both must be given together, or neither.
    """
    if samples is None or samplerate is None:
        samples, samplerate, _full_scale = read_pcm(wav_path)
        if params.denoise:
            samples = denoised_samples(samples, samplerate, params.cutoff_hz)

    # Clamp to the signal's own length -- without this, a very short (or
    # truncated/corrupt) recording makes nperseg > len(samples), and
    # scipy.signal.spectrogram silently shrinks it back down itself but
    # raises a UserWarning while doing so. This project's test output must
    # stay warning-free (a warning is a defect), so the clamp happens here,
    # before scipy ever sees an oversized nperseg -- not just to keep tests
    # quiet, but because a genuinely short/corrupt file reaching this code
    # in production shouldn't warn either.
    nperseg = min(max(int(samplerate * params.window_ms / 1000), 8), len(samples))
    noverlap = int(nperseg * params.overlap)
    freqs, times, sxx = signal.spectrogram(
        samples,
        fs=samplerate,
        nperseg=nperseg,
        noverlap=noverlap,
    )

    # Never render frequency bins above this recording's own Nyquist limit --
    # a recording at a different sample rate must not be asked to render data
    # that doesn't exist (design spec §4).
    max_freq = effective_max_freq_hz(samplerate, params)
    keep = freqs <= max_freq
    freqs = freqs[keep]
    sxx = sxx[keep, :]

    if params.denoise:
        # `median_size=(1, 1)` disables the selective edge-preserving median-filter step (see
        # subtract_background_noise's own docstring). Measured 2026-09-14 against a real field
        # recording (audio-domain spectral_gate now runs upstream of this, see media/denoise.py)
        # as contributing a <1% change in background speckle (std 55.77 without the median step
        # vs 56.27 with it, over a representative quiet region) once the audio itself is already
        # gated -- the median filter's own known tradeoff (still-visible call smudging) is no
        # longer worth paying for here. The over-subtraction step above it still runs and still
        # does the real cleanup. `subtract_background_noise`'s default (7, 7) is left as-is
        # rather than changed here: it's a one-line revert if this call site's own tuning turns
        # out wrong, or if a future caller needs the median step's stronger speckle removal.
        sxx = subtract_background_noise(sxx, median_size=(1, 1))

    # dB relative to this recording's own loudest bin ABOVE the low-frequency noise-band
    # boundary (never the low band itself, which can otherwise dominate and darken the whole
    # image -- design spec's peak-exclusion fix, unconditional, not gated by `denoise`), clipped
    # to a fixed dynamic-range window and normalised to [0, 1] -- NOT `log1p` on raw
    # power. Bat-call power spectra are almost entirely background well
    # below 1.0 in scipy's PSD units; `log1p(x) ~= x` for `x << 1`, so a
    # plain `log1p` + min-max normalise barely distinguishes the noise floor
    # from itself and crushes it into a razor-thin band at the bottom of the
    # range while only the single loudest bin of an actual call reaches any
    # visible brightness -- production spectrograms rendered almost entirely
    # black with a few faint slivers, confirmed against real recordings
    # 2026-08-26. `10*log10` treats small values proportionally instead, the
    # same convention Audacity/batogram/Kaleidoscope's spectrogram views use.
    peak = (
        sxx[freqs >= params.cutoff_hz].max()
        if (freqs >= params.cutoff_hz).any()
        else sxx.max()
    )
    if peak > 0:
        db = 10 * np.log10(np.maximum(sxx, 1e-300) / peak)
        clipped = np.clip(db, -params.dynamic_range_db, 0.0)
        normalised = (clipped + params.dynamic_range_db) / params.dynamic_range_db
    else:
        # All-zero signal: no peak to be relative to. Every bin is equally
        # "silent" -- render the palette floor throughout, not a div-by-zero.
        normalised = np.zeros_like(sxx)

    # Flip vertically: spectrogram's frequency axis increases with row index,
    # but an image's row 0 is its TOP -- without this, low frequencies would
    # render at the top of the image, high frequencies at the bottom.
    indices = (np.flipud(normalised) * 255).astype(np.uint8)
    lut = _palette_lut(params.palette)
    rgb = lut[indices]

    full_image = Image.fromarray(rgb, mode="RGB")
    return FullSpectrogramImage(
        image=full_image,
        times=times,
        freqs=freqs,
        duration_s=len(samples) / samplerate,
        max_freq_hz=max_freq,
    )


def _resample_onto_axes(
    full: FullSpectrogramImage,
    out_size: tuple[int, int],
    *,
    time_range_s: tuple[float, float],
) -> Image.Image:
    """Resample the native-resolution STFT image so output pixel edges sit exactly on real
    time/frequency: x = 0 is `time_range_s[0]`, x = width is `time_range_s[1]`, y = height is
    0 Hz and y = 0 is `full.max_freq_hz`.

    The STFT image is a grid of CELLS whose centres are at `times[i]` / `freqs[b]`. A plain
    resize of the whole image stretches the first and last cell to the image edges instead, which
    put every reading up to half a cell off: a bin up to 0.33 kHz (1.5 ms window) from where the
    frequency axis places its frequency, and a column up to ~0.6 ms from where the time axis (and
    the oscillogram, which is exact) places its instant -- growing linearly from one edge of the
    image to the other. Here the source box is derived from the STFT's own `times`/`freqs`, so a
    cell centre lands on its own axis value; the source region lies partly outside the image at
    the ends (the first window is centred at window/2, not 0), so it is edge-replicated there.

    Only the crop the box needs is materialised, plus a margin for the resampling kernel: the
    kernel must see real neighbouring pixels just outside a tile's box, or two independently
    rendered tiles disagree at their seam (each would clamp its own edge column instead)."""
    stft_image = full.image
    out_w, out_h = out_size
    n_rows, n_cols = stft_image.height, stft_image.width
    if n_cols < 2 or n_rows < 2:
        # A single column/bin has no hop/bin width to place it by -- nothing to align.
        return stft_image.resize(out_size)

    first_time_s = float(full.times[0])
    hop_s = float(full.times[1] - full.times[0])
    bin_hz = float(full.freqs[1] - full.freqs[0])
    start_s, end_s = time_range_s

    # Source coordinates where cell i's CENTRE is at i + 0.5 (columns left->right; rows top->
    # bottom, the image being flipped so row 0 is the highest kept bin).
    left = (start_s - first_time_s) / hop_s + 0.5
    right = (end_s - first_time_s) / hop_s + 0.5
    top = n_rows - (full.max_freq_hz / bin_hz + 0.5)
    bottom = n_rows - 0.5

    margin_cols = math.ceil(2 * max(1.0, (right - left) / out_w)) + 2
    margin_rows = math.ceil(2 * max(1.0, (bottom - top) / out_h)) + 2
    col0 = math.floor(left) - margin_cols
    row0 = math.floor(top) - margin_rows
    cols = np.clip(np.arange(col0, math.ceil(right) + margin_cols), 0, n_cols - 1)
    rows = np.clip(np.arange(row0, math.ceil(bottom) + margin_rows), 0, n_rows - 1)
    crop = np.asarray(stft_image)[np.ix_(rows, cols)]
    return Image.fromarray(crop).resize(
        out_size, box=(left - col0, top - row0, right - col0, bottom - row0)
    )


def render_spectrogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: SpectrogramParams = SpectrogramParams(),
    time_range_s: tuple[float, float] | None = None,
    full_image: FullSpectrogramImage | None = None,
) -> None:
    """Render `wav_path`'s spectrogram to `out_path` as a WebP image.

    See `render_full_spectrogram_image` for the STFT/normalize/palette details -- this function
    calls it internally (unless `full_image` is already given, see below), then resizes to
    `params.width_px`/`height_px` and writes the result.

    Writes to a temp file in `out_path`'s parent directory, then `os.replace`s
    onto `out_path` -- atomic on the same filesystem, so a concurrent reader
    never sees a partial file and two concurrent writers never interleave
    (design spec §7's duplicate-enqueue protection is the queue-level half of
    this; this is the filesystem-level half).

    `time_range_s`, if given, renders only that `(start_s, end_s)` slice of
    the recording -- for tiling a long recording into multiple images that
    together stay under WebP's hard pixel-dimension limit. The STFT and
    `peak` are still computed from the WHOLE file first, so normalisation
    never drifts between tiles of the same recording -- only the final
    slice-and-resize step is narrowed to `time_range_s`. Slicing the input
    samples before computing the peak would make each tile self-normalise
    independently, so the same call could render at different brightness
    depending purely on which tile boundary it happened to fall inside.

    `full_image`, if given, is used instead of calling `render_full_spectrogram_image`
    internally -- lets a caller rendering many tiles of the same recording (the
    recording-detail page) compute it once and pass it to every tile's call, skipping the
    repeated STFT. Must be a value `render_full_spectrogram_image(wav_path, params)` actually
    returned (for the SAME `wav_path`/`params`) -- passing a mismatched image silently renders
    the wrong content, since nothing here re-derives it from `wav_path` to check.
    """
    if full_image is None:
        full_image = render_full_spectrogram_image(wav_path, params)
    if time_range_s is None:
        time_range_s = (0.0, full_image.duration_s)
    image = _resample_onto_axes(
        full_image,
        (params.width_px, params.height_px),
        time_range_s=time_range_s,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, suffix=".webp.tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        image.save(tmp_path, format="WEBP")
        os.replace(tmp_path, out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
