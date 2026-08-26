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


def read_pcm(wav_path: Path) -> tuple[np.ndarray, int]:
    """Read mono or multi-channel 16-bit PCM as a 1-D float array (channels
    averaged down to mono) plus the file's own sample rate."""
    with wave.open(str(wav_path), "rb") as wav:
        n_channels = wav.getnchannels()
        samplerate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)
    return samples, samplerate
