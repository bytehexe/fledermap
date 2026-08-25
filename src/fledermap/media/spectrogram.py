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
import wave
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import signal


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

    @property
    def params_hash(self) -> str:
        payload = "|".join(str(getattr(self, f.name)) for f in fields(self))
        return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


def _read_pcm(wav_path: Path) -> tuple[np.ndarray, int]:
    """Read mono or multi-channel 16-bit PCM as a 1-D float array (channels
    averaged down to mono for spectrogram purposes) plus the file's own
    sample rate."""
    with wave.open(str(wav_path), "rb") as wav:
        n_channels = wav.getnchannels()
        samplerate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)
    return samples, samplerate


def render_spectrogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: SpectrogramParams | None = None,
) -> None:
    """Render `wav_path`'s spectrogram to `out_path` as a WebP image.

    STFT via `scipy.signal.spectrogram`, log-magnitude normalised to [0, 1],
    rendered as a single-channel (grayscale) image -- the simplest possible
    colormap, revisable later without a schema change (`params_hash` exists
    precisely so a colour-scheme change would invalidate old renders cleanly).

    Writes to a temp file in `out_path`'s parent directory, then `os.replace`s
    onto `out_path` -- atomic on the same filesystem, so a concurrent reader
    never sees a partial file and two concurrent writers never interleave
    (design spec §7's duplicate-enqueue protection is the queue-level half of
    this; this is the filesystem-level half).
    """
    if params is None:
        params = SpectrogramParams()
    samples, samplerate = _read_pcm(wav_path)

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
    freqs, _times, sxx = signal.spectrogram(
        samples,
        fs=samplerate,
        nperseg=nperseg,
        noverlap=noverlap,
    )

    # Never render frequency bins above this recording's own Nyquist limit --
    # a recording at a different sample rate must not be asked to render data
    # that doesn't exist (design spec §4).
    max_freq = min(params.max_freq_hz, samplerate / 2)
    keep = freqs <= max_freq
    sxx = sxx[keep, :]

    log_mag = np.log1p(sxx)
    span = log_mag.max() - log_mag.min()
    normalised = (
        (log_mag - log_mag.min()) / span if span > 0 else np.zeros_like(log_mag)
    )

    # Flip vertically: spectrogram's frequency axis increases with row index,
    # but an image's row 0 is its TOP -- without this, low frequencies would
    # render at the top of the image, high frequencies at the bottom.
    pixels = (np.flipud(normalised) * 255).astype(np.uint8)
    image = Image.fromarray(pixels, mode="L").resize(
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
