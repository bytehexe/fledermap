"""On-demand sound-level spectrum for the recording-details page's Analysis view (design spec
docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md §5). Pure, no I/O -- unlike
`spectrogram.py`/`oscillogram.py`, this returns data for a JSON endpoint to serialize, not a
rendered image, since it's computed fresh per request rather than cached derived media."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import welch


@dataclass(frozen=True)
class SoundLevelSpectrum:
    freqs_hz: np.ndarray
    db: np.ndarray  # relative to this spectrum's own peak -- 0 dB at the peak, matching
    # spectrogram.py's self-relative convention (CLAUDE.md's "Derived media rendering").
    peak_freq_hz: float


def compute_sound_level_spectrum(
    samples: np.ndarray,
    samplerate_hz: float,
    dynamic_range_db: float = 80.0,
    peak_min_freq_hz: float = 0.0,
) -> SoundLevelSpectrum:
    # `nperseg` capped at the sample count: scipy.signal.welch's own default (256) raises for a
    # shorter input (e.g. a narrow Select-tool span), and Welch's method needs at least one full
    # segment to produce any output at all. 2048 (vs. the previous 256) gives ~125Hz-wide bins at
    # a 256kHz samplerate instead of ~1kHz-wide ones -- fine enough to distinguish species with
    # peak frequencies only a few kHz apart, still capped for a short Select-tool span.
    nperseg = min(2048, len(samples))
    freqs_hz, psd = welch(samples, fs=samplerate_hz, nperseg=nperseg)
    # The peak SEARCH excludes low-frequency wind/handling noise (spectrogram.py's own peak-
    # exclusion convention, unconditional -- not gated by whether Denoise is on), which otherwise
    # dominates and produces a meaningless "peak" -- but the DISPLAYED curve (`db` below) is still
    # normalized relative to that peak across the FULL spectrum, so the shape shown is unchanged;
    # only which bin counts as "the peak" changes.
    above_cutoff = freqs_hz >= peak_min_freq_hz
    search_psd = psd[above_cutoff] if above_cutoff.any() else psd
    search_freqs_hz = freqs_hz[above_cutoff] if above_cutoff.any() else freqs_hz
    peak_index_in_search = int(np.argmax(search_psd))
    peak_freq_hz = float(search_freqs_hz[peak_index_in_search])
    peak_psd = search_psd[peak_index_in_search]
    if peak_psd > 0:
        # Normal case: sound present. Scale relative to peak, matching spectrogram.py's
        # self-relative convention (CLAUDE.md's "Derived media rendering").
        db = 10 * np.log10(np.maximum(psd, 1e-300) / peak_psd)
        db = np.clip(db, -dynamic_range_db, 0.0)
    else:
        # All-zero signal (e.g., silent audio in a narrow Select-tool span): no peak to be
        # relative to. Every bin is equally silent -- report them at the floor of the dB scale
        # (not 0.0, which is the loud end) rather than dividing by zero.
        db = np.full_like(psd, -dynamic_range_db)
    return SoundLevelSpectrum(freqs_hz=freqs_hz, db=db, peak_freq_hz=peak_freq_hz)
