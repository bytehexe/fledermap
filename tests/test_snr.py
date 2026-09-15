"""Plain unit tests for `ingest.snr.compute_snr_db` -- no DB, no Flask app,
matching every other pure `ingest`/`media` module test."""

from __future__ import annotations

import numpy as np

from fledermap.ingest.snr import compute_snr_db

_SAMPLERATE_HZ = 256_000.0


def _white_noise(*, n_samples: int, amplitude: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(scale=amplitude, size=n_samples)


def test_compute_snr_db_is_higher_with_a_loud_call_burst_than_noise_alone() -> None:
    # `spectral_snr`'s noise-profile method (Towsey 2013/2017) estimates the
    # background floor per frequency bin from its typical (modal) level over
    # time -- it's built to separate a brief, loud call from a continuous
    # background, not to compare two equally-continuous signals. A tone
    # present for the recording's *entire* duration IS the new per-bin
    # background level, not a signal standing out above it, so the burst
    # here only covers a fraction of the recording, the way a real bat call
    # does against room/wind noise.
    n_samples = int(_SAMPLERATE_HZ * 0.1)
    noise = _white_noise(n_samples=n_samples, amplitude=0.05, seed=0)

    burst_start, burst_end = int(n_samples * 0.4), int(n_samples * 0.5)
    t_burst = np.arange(burst_end - burst_start) / _SAMPLERATE_HZ
    burst = np.zeros(n_samples)
    burst[burst_start:burst_end] = 1.0 * np.sin(2 * np.pi * 40_000 * t_burst)

    noise_only_snr = compute_snr_db(noise, _SAMPLERATE_HZ)
    with_burst_snr = compute_snr_db(noise + burst, _SAMPLERATE_HZ)

    assert with_burst_snr > noise_only_snr


def test_compute_snr_db_returns_a_plain_float() -> None:
    samples = _white_noise(n_samples=int(_SAMPLERATE_HZ * 0.1), amplitude=0.05, seed=1)

    result = compute_snr_db(samples, _SAMPLERATE_HZ)

    # Not np.float64 -- psycopg2 has no adapter for it (CLAUDE.md's "Database"
    # section); `isinstance(x, float)` alone would not catch this, since
    # np.float64 is itself a subclass of the builtin float.
    assert type(result) is float
