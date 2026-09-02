"""Locked scale for the recording details page (design spec
2026-09-01-fledermap-recording-details-page-design.md, section 1). Pure --
no DB, no filesystem -- this is the one place both the detail-image serving
routes and the page route compute these numbers, so they can never disagree
about a recording's width.
"""

from __future__ import annotations

from dataclasses import dataclass

from fledermap.media.oscillogram import OscillogramParams
from fledermap.media.spectrogram import SpectrogramParams, stft_hop_samples

# Chosen 2026-09-02 from a two-round visual comparison against a real field recording (design
# spec's dated addendum) -- detail-page-only, deliberately independent of `SpectrogramParams`'s
# own `window_ms`/`overlap` defaults, which the drawer/overview's cached renders use instead
# (Janna's ruling: the overview compresses far more time into the same screen width, so there's
# no reason to assume one FFT window suits both -- tune this page for this page). Defined before
# `DETAIL_PX_PER_MS` below, which derives from these two.
DETAIL_WINDOW_MS = 1.5
DETAIL_OVERLAP = 0.85

# This project's EMT device rate -- the reference samplerate `DETAIL_PX_PER_MS` below is computed
# against. A recording at a different samplerate shifts slightly off the exact 1:1 point that
# computation targets -- unavoidable for a single fixed scale shared across every recording, same
# tradeoff the existing `DETAIL_MAX_FREQ_KHZ` ceiling below already accepts.
_DETAIL_REFERENCE_SAMPLERATE_HZ = 256_000

# Derived from the Skiba identification guide's 10ms:40kHz convention against
# a ~15cm target print height (96dpi CSS px) -- explicitly a tunable
# starting point, not a final number (design spec Decisions): refine once
# real recordings are actually on screen. Revised 2026-09-02 (19.0 -> 12.0 -> exact 1:1) across
# three rounds of visual comparison against a real field recording -- see the design spec's dated
# addenda for the full reasoning.
#
# Round 3 pushed the scale to the exact 1:1 point with the real STFT hop: computed via
# `stft_hop_samples` (`media/spectrogram.py`) -- the same `nperseg`/`noverlap` formula
# `render_spectrogram` uses internally -- rather than a hand-copied literal, so a later change to
# `DETAIL_WINDOW_MS`/`DETAIL_OVERLAP` recomputes this automatically instead of silently going
# stale (see that function's docstring for why a second, hand-copied formula is a real risk here).
# One real STFT column maps to exactly one display pixel: no further squeeze is possible without
# discarding real time resolution.
DETAIL_PX_PER_MS = (
    _DETAIL_REFERENCE_SAMPLERATE_HZ
    / stft_hop_samples(
        _DETAIL_REFERENCE_SAMPLERATE_HZ, DETAIL_WINDOW_MS, DETAIL_OVERLAP
    )
    / 1000
)
DETAIL_PX_PER_KHZ = 4.7
# A ceiling, not a promise every recording reaches it -- clamped to the
# recording's own Nyquist limit below, same convention
# `spectrogram.effective_max_freq_hz` already uses for the drawer.
DETAIL_MAX_FREQ_KHZ = 120.0
# Comfortably under WebP's hard 16383px encode-dimension limit -- the whole reason tiling
# exists: at DETAIL_PX_PER_MS (~4.4138), any recording longer than ~3.71s would otherwise produce
# a spectrogram wider than that limit (design spec's 2026-09-01 tiling addendum).
DETAIL_MAX_TILE_WIDTH_PX = 8000


@dataclass(frozen=True)
class DetailTile:
    index: int
    start_px: int
    width_px: int


def detail_tiles(total_width_px: int) -> list[DetailTile]:
    """Split a recording's full locked-scale width into fixed-width chunks, each safely under
    WebP's pixel limit. The last tile absorbs whatever remainder doesn't fill a full
    `DETAIL_MAX_TILE_WIDTH_PX` chunk -- covers the full width with no gaps and no overlap."""
    tiles = []
    start = 0
    index = 0
    while start < total_width_px:
        width = min(DETAIL_MAX_TILE_WIDTH_PX, total_width_px - start)
        tiles.append(DetailTile(index=index, start_px=start, width_px=width))
        start += width
        index += 1
    return tiles


@dataclass(frozen=True)
class DetailParams:
    spectrogram: SpectrogramParams
    oscillogram: OscillogramParams
    max_freq_khz: float
    tiles: list[DetailTile]


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
        window_ms=DETAIL_WINDOW_MS,
        overlap=DETAIL_OVERLAP,
    )
    oscillogram = OscillogramParams(width_px=width_px)
    return DetailParams(
        spectrogram=spectrogram,
        oscillogram=oscillogram,
        max_freq_khz=max_freq_hz / 1000,
        tiles=detail_tiles(width_px),
    )
