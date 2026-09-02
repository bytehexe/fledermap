"""Reading 16-bit PCM WAV audio as a plain float array. Shared by
`spectrogram.py` and `oscillogram.py` -- both need the same raw samples, and
a second, drifted copy of this is exactly the kind of thing that goes stale
silently (see `media/paths.py`'s docstring on writer/reader formula
agreement for the general shape of that risk).
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


class UnreadableWavError(Exception):
    """Raised by `read_pcm` for a WAV file that exists on disk but can't be
    decoded as PCM audio -- a corrupt header, or a file truncated mid-sample.
    Callers that already 404 for a *missing* source file (recording detail
    page's tile routes) should treat this the same way rather than letting
    it surface as a raw 500: from a client's perspective "the file is there
    but unreadable" and "the file isn't there" are the same unusable state.
    """


def read_pcm(wav_path: Path) -> tuple[np.ndarray, int]:
    """Read mono or multi-channel 16-bit PCM as a 1-D float array (channels
    averaged down to mono) plus the file's own sample rate.

    Raises `UnreadableWavError` for a file that isn't a valid RIFF/WAVE
    container (`wave.open` itself raises `wave.Error`/`EOFError`), or one
    truncated mid-sample -- `wave.readframes` silently returns however many
    bytes are actually on disk rather than raising, so a short read only
    surfaces once `np.frombuffer`/`reshape` see a byte count that doesn't
    evenly divide into samples (`ValueError`).
    """
    try:
        with wave.open(str(wav_path), "rb") as wav:
            n_channels = wav.getnchannels()
            samplerate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)
    except (wave.Error, EOFError, ValueError) as exc:
        raise UnreadableWavError(f"cannot read PCM from {wav_path}: {exc}") from exc
    return samples, samplerate
