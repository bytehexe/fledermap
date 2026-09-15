"""Signal-to-noise ratio estimation for ingested recordings (Obsidian backlog
"Noise" section, Step 1: just display SNR -- the noise classifier itself is a
later step). Pure: no DB, no queue awareness, matching every other `ingest/`
module.
"""

from __future__ import annotations

import numpy as np
from maad.sound import spectral_snr
from scipy import signal

from fledermap.media.spectrogram import DEFAULT_SPECTROGRAM_PARAMS


def compute_snr_db(samples: np.ndarray, samplerate_hz: float) -> float:
    """Broadband SNR in dB (Towsey 2013/2017 noise-profile method, via
    `maad.sound.spectral_snr`), from the same STFT window/overlap convention
    `media/spectrogram.py` renders with -- one shared definition of "the
    spectrogram" rather than a second, silently different one here.
    """
    nperseg = min(
        max(int(samplerate_hz * DEFAULT_SPECTROGRAM_PARAMS.window_ms / 1000), 8),
        len(samples),
    )
    noverlap = int(nperseg * DEFAULT_SPECTROGRAM_PARAMS.overlap)
    _, _, sxx = signal.spectrogram(
        samples,
        fs=samplerate_hz,
        nperseg=nperseg,
        noverlap=noverlap,
    )
    _, _, snr_db, _, _, _ = spectral_snr(sxx)
    return float(snr_db)
