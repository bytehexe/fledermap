"""Spectrogram rendering. Pure: reads a WAV file, writes a WebP image. No DB,
no queue awareness (design spec §3) -- `jobs/tasks.py` is the only caller.

Written fresh against scipy/numpy/Pillow rather than ported from batogram
(design spec §2, decision P3-1): batogram is a Tkinter GUI application with no
stable, separable library API, not a clean porting target the way
mkmapdiary's LocalProjection/GeoCluster were in Phase 2.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import signal

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


def render_spectrogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: SpectrogramParams = SpectrogramParams(),
    time_range_s: tuple[float, float] | None = None,
) -> None:
    """Render `wav_path`'s spectrogram to `out_path` as a WebP image.

    STFT via `scipy.signal.spectrogram`, power converted to dB relative to
    the recording's own peak, clipped to `params.dynamic_range_db` and
    mapped through `params.palette`'s colour lookup table. Both are ordinary
    `SpectrogramParams` fields, so changing either one is just a settings
    change -- `params_hash` exists precisely so that invalidates old renders
    cleanly, without a schema change or a manual cache bust.

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
    """
    samples, samplerate = read_pcm(wav_path)

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
    sxx = sxx[keep, :]

    # dB relative to this recording's own loudest bin, clipped to a fixed
    # dynamic-range window and normalised to [0, 1] -- NOT `log1p` on raw
    # power. Bat-call power spectra are almost entirely background well
    # below 1.0 in scipy's PSD units; `log1p(x) ~= x` for `x << 1`, so a
    # plain `log1p` + min-max normalise barely distinguishes the noise floor
    # from itself and crushes it into a razor-thin band at the bottom of the
    # range while only the single loudest bin of an actual call reaches any
    # visible brightness -- production spectrograms rendered almost entirely
    # black with a few faint slivers, confirmed against real recordings
    # 2026-08-26. `10*log10` treats small values proportionally instead, the
    # same convention Audacity/batogram/Kaleidoscope's spectrogram views use.
    peak = sxx.max()
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

    if time_range_s is not None:
        # `rgb.shape[1]` (the STFT's own column count) is always >= 1 for any nonzero-length
        # signal (the `nperseg` clamp above guarantees at least one window fits). Clamping
        # `end_idx` to be at least `start_idx + 1` guarantees a non-empty slice even for a very
        # narrow tile (the last tile in a recording whose width doesn't divide evenly by
        # `DETAIL_MAX_TILE_WIDTH_PX` can be as little as 1px wide -- narrower than a single STFT
        # column's own time resolution) -- without this, `Image.fromarray` on a zero-width array
        # raises rather than degrading gracefully.
        start_s, end_s = time_range_s
        start_idx = max(0, min(int(np.searchsorted(times, start_s)), rgb.shape[1] - 1))
        end_idx = max(
            start_idx + 1, min(int(np.searchsorted(times, end_s)), rgb.shape[1])
        )
        rgb = rgb[:, start_idx:end_idx, :]

    image = Image.fromarray(rgb, mode="RGB").resize(
        (params.width_px, params.height_px),
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
