"""Locked scale for the recording details page (design spec
2026-09-01-fledermap-recording-details-page-design.md, section 1). Pure --
no DB, no filesystem -- this is the one place both the detail-image serving
routes and the page route compute these numbers, so they can never disagree
about a recording's width.
"""

from __future__ import annotations

from dataclasses import dataclass

from fledermap.media.oscillogram import OscillogramParams
from fledermap.media.spectrogram import SpectrogramParams

# Derived from the Skiba identification guide's 10ms:40kHz convention against
# a ~15cm target print height (96dpi CSS px) -- explicitly a tunable
# starting point, not a final number (design spec Decisions): refine once
# real recordings are actually on screen.
DETAIL_PX_PER_MS = 19.0
DETAIL_PX_PER_KHZ = 4.7
# A ceiling, not a promise every recording reaches it -- clamped to the
# recording's own Nyquist limit below, same convention
# `spectrogram.effective_max_freq_hz` already uses for the drawer.
DETAIL_MAX_FREQ_KHZ = 120.0


@dataclass(frozen=True)
class DetailParams:
    spectrogram: SpectrogramParams
    oscillogram: OscillogramParams
    max_freq_khz: float


def detail_params(duration_s: float, samplerate_hz: float) -> DetailParams:
    """Both images share one computed `width_px`: a recording twice as long
    renders twice as wide, so panning through it at the locked
    `DETAIL_PX_PER_MS` scale always means "the same span of time is the
    same span of pixels" -- the entire point of a locked scale (design spec
    section 1)."""
    width_px = round(duration_s * 1000 * DETAIL_PX_PER_MS)
    max_freq_hz = min(DETAIL_MAX_FREQ_KHZ * 1000, samplerate_hz / 2)
    height_px = round((max_freq_hz / 1000) * DETAIL_PX_PER_KHZ)
    spectrogram = SpectrogramParams(
        width_px=width_px,
        height_px=height_px,
        max_freq_hz=max_freq_hz,
    )
    oscillogram = OscillogramParams(width_px=width_px)
    return DetailParams(
        spectrogram=spectrogram,
        oscillogram=oscillogram,
        max_freq_khz=max_freq_hz / 1000,
    )
