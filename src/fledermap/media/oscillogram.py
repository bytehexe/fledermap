"""Oscillogram (waveform envelope) rendering. Pure: reads a WAV file, writes
a WebP image. No DB, no queue awareness (design spec §3), mirroring
`spectrogram.py`'s own shape exactly -- the drawer shows both side by side,
sharing the recording's time axis (design spec §4).

Rendered at the same `width_px` as the spectrogram (both default to 1024) so
the two images line up pixel-for-pixel on the time axis when displayed at
the same on-screen width -- neither module imports the other's params to
enforce this; it's a convention documented here and in `spectrogram.py`.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from PIL import Image

from fledermap.media.wav_pcm import read_pcm


@dataclass(frozen=True)
class OscillogramParams:
    """Every field that affects rendered output -- see `SpectrogramParams`
    for why `params_hash` exists and what it protects."""

    width_px: int = 1024
    # Small on purpose (design decision: "very small, maybe 1cm roughly") --
    # this is a compact waveform strip above the spectrogram, not a
    # standalone waveform view.
    height_px: int = 48
    line_color: tuple[int, int, int] = (0, 0, 0)
    background_color: tuple[int, int, int] = (255, 255, 255)

    @property
    def params_hash(self) -> str:
        payload = "|".join(str(getattr(self, f.name)) for f in fields(self))
        return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


DEFAULT_OSCILLOGRAM_PARAMS = OscillogramParams()


def render_oscillogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: OscillogramParams = OscillogramParams(),
) -> None:
    """Render `wav_path`'s peak-envelope waveform to `out_path` as a WebP
    image: for each output column, the min and max sample in that column's
    time slice, drawn as a vertical bar -- the standard "peak envelope"
    waveform display every audio editor uses, rather than plotting every
    individual sample (meaningless at this resolution: a 0.05s call at
    256kHz is ~12800 samples compressed into ~1024 columns).

    Amplitude is normalised to THIS recording's own peak, not a fixed
    int16-range reference -- a quiet call must still be visible, not a
    flat, barely-there line just because it happened to be recorded far
    from the microphone. This mirrors `spectrogram.py`'s dB-relative-to-own-
    peak normalisation for the same reason.

    Writes atomically via a temp file + `os.replace`, matching
    `spectrogram.py`'s own write path.
    """
    samples, _samplerate = read_pcm(wav_path)
    width, height = params.width_px, params.height_px

    peak = np.abs(samples).max() if samples.size else 0.0
    canvas = np.full((height, width, 3), params.background_color, dtype=np.uint8)
    mid = height / 2.0

    if peak > 0 and samples.size:
        # Bucket samples into `width` columns (the last bucket absorbs any
        # remainder from an uneven split -- same "clamp, don't crash on a
        # short/odd-length signal" spirit as `spectrogram.py`'s nperseg
        # clamp).
        bucket_edges = np.linspace(0, samples.size, width + 1).astype(int)
        for col in range(width):
            start, end = bucket_edges[col], bucket_edges[col + 1]
            if start == end:
                continue
            bucket = samples[start:end]
            lo = mid - (bucket.min() / peak) * mid
            hi = mid - (bucket.max() / peak) * mid
            row_lo, row_hi = sorted((int(round(lo)), int(round(hi))))
            row_hi = max(row_hi, row_lo)  # a single-sample bucket: draw 1px
            canvas[row_lo : row_hi + 1, col] = params.line_color
    else:
        # Silence: draw a flat centre line rather than nothing, so the
        # oscillogram still reads as "recording present, just quiet" instead
        # of a blank strip indistinguishable from a rendering failure.
        canvas[int(mid), :] = params.line_color

    image = Image.fromarray(canvas, mode="RGB")

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
