"""On-demand sound-level spectrum for the recording-details page's Analysis view (design spec
docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md §5). Pure, no I/O -- unlike
`spectrogram.py`/`oscillogram.py`, this returns data for a JSON endpoint to serialize, not a
rendered image, since it's computed fresh per request rather than cached derived media."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, welch

# What counts as a "major" local maximum (spec 2026-09-18-fledermap-sound-level-peaks-design.md).
# Prominence alone is not enough: a hump at the noise floor can be 10 dB prominent (a real
# recording's -34 dB hump was), so a level floor re the search peak rejects those. Both values
# and the cap were checked against three real whole-file spectra; short Select spans (few Welch
# segments, noisier curve) are the case to re-check.
PEAK_MIN_PROMINENCE_DB = 8.0
PEAK_MIN_LEVEL_DB = -20.0
MAX_PEAKS = 5


@dataclass(frozen=True)
class SoundLevelSpectrum:
    freqs_hz: np.ndarray
    db: np.ndarray  # relative to this spectrum's own peak -- 0 dB at the peak, matching
    # spectrogram.py's self-relative convention (CLAUDE.md's "Derived media rendering").
    # None when the search's loudest bin is the FIRST bin above `peak_min_freq_hz`: the low-band
    # exclusion cut through a component that is still falling there (wind/handling noise skirt),
    # so that bin reflects where the search window starts, not a spectral peak. Reporting it
    # would present the cutoff itself (e.g. "5.0 kHz") as a measurement.
    peak_freq_hz: float | None
    # Ascending; the global peak (when `peak_freq_hz` is not None) plus the other major local
    # maxima -- see `find_major_peaks_hz`.
    peaks_freq_hz: list[float]


def find_major_peaks_hz(
    freqs_hz: np.ndarray, db: np.ndarray, peak_min_freq_hz: float = 0.0
) -> list[float]:
    """Frequencies (ascending) of the spectrum's major local maxima, at most `MAX_PEAKS`.

    Searches only at/above `peak_min_freq_hz`, the same region the global-peak search uses. The
    global maximum of that region is always included (even if it is not prominent by itself, e.g.
    it sits on a broad shoulder) -- unless it is the region's first bin, where the exclusion cut
    through a still-falling component (see `SoundLevelSpectrum.peak_freq_hz`). `find_peaks` never
    returns an endpoint, so no other peak can land on that boundary either. When more than
    `MAX_PEAKS` qualify, the most prominent are kept.
    """
    above_cutoff = freqs_hz >= peak_min_freq_hz
    low_band_was_excluded = bool(above_cutoff.any() and not above_cutoff.all())
    search_freqs_hz = freqs_hz[above_cutoff] if above_cutoff.any() else freqs_hz
    search_db = db[above_cutoff] if above_cutoff.any() else db
    global_index = int(np.argmax(search_db))
    global_on_boundary = low_band_was_excluded and global_index == 0

    found, properties = find_peaks(
        search_db, prominence=PEAK_MIN_PROMINENCE_DB, height=PEAK_MIN_LEVEL_DB
    )
    # A flat-topped global maximum makes `find_peaks` report the plateau's middle bin while
    # `argmax` reports its first -- the same peak, so it must not be listed twice.
    candidates = [
        (float(prominence), int(index))
        for index, prominence in zip(found, properties["prominences"], strict=True)
        if global_on_boundary or abs(int(index) - global_index) > 1
    ]
    candidates.sort(reverse=True)
    kept: list[int] = [index for _, index in candidates]
    if global_on_boundary:
        kept = kept[:MAX_PEAKS]
    else:
        kept = [global_index, *kept[: MAX_PEAKS - 1]]
    return [float(search_freqs_hz[index]) for index in sorted(kept)]


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
    low_band_was_excluded = bool(above_cutoff.any() and not above_cutoff.all())
    peak_on_search_boundary = low_band_was_excluded and peak_index_in_search == 0
    peak_freq_hz = (
        None
        if peak_on_search_boundary
        else float(search_freqs_hz[peak_index_in_search])
    )
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
    peaks_freq_hz = find_major_peaks_hz(freqs_hz, db, peak_min_freq_hz)
    return SoundLevelSpectrum(
        freqs_hz=freqs_hz, db=db, peak_freq_hz=peak_freq_hz, peaks_freq_hz=peaks_freq_hz
    )
