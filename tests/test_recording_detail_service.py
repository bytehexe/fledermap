from __future__ import annotations

from fledermap.services.recording_detail import (
    DETAIL_MAX_FREQ_KHZ,
    DETAIL_MAX_TILE_WIDTH_PX,
    DETAIL_OVERLAP,
    DETAIL_PX_PER_KHZ,
    DETAIL_PX_PER_MS,
    DETAIL_WINDOW_MS,
    DetailTile,
    detail_params,
    detail_tiles,
)


def test_detail_params_computes_width_from_duration_and_px_per_ms() -> None:
    params = detail_params(duration_s=2.0, samplerate_hz=256_000)

    assert params.spectrogram.width_px == round(2.0 * 1000 * DETAIL_PX_PER_MS)
    assert params.oscillogram.width_px == params.spectrogram.width_px


def test_detail_params_uses_the_ceiling_when_samplerate_is_high_enough() -> None:
    params = detail_params(duration_s=1.0, samplerate_hz=256_000)

    assert params.max_freq_khz == DETAIL_MAX_FREQ_KHZ
    assert params.spectrogram.height_px == round(
        DETAIL_MAX_FREQ_KHZ * DETAIL_PX_PER_KHZ
    )


def test_detail_params_clamps_to_nyquist_below_the_ceiling() -> None:
    # 44_100 Hz samplerate -> Nyquist 22_050 Hz = 22.05 kHz, well under the
    # 120 kHz ceiling -- the clamp must win, not the ceiling.
    params = detail_params(duration_s=1.0, samplerate_hz=44_100)

    assert params.max_freq_khz == 22.05
    assert params.spectrogram.height_px == round(22.05 * DETAIL_PX_PER_KHZ)


def test_detail_tiles_returns_one_tile_for_a_short_recording() -> None:
    tiles = detail_tiles(total_width_px=500)

    assert tiles == [DetailTile(index=0, start_px=0, width_px=500)]


def test_detail_tiles_splits_a_wide_recording_at_the_max_tile_width() -> None:
    total = DETAIL_MAX_TILE_WIDTH_PX * 2 + 300

    tiles = detail_tiles(total_width_px=total)

    assert tiles == [
        DetailTile(index=0, start_px=0, width_px=DETAIL_MAX_TILE_WIDTH_PX),
        DetailTile(
            index=1,
            start_px=DETAIL_MAX_TILE_WIDTH_PX,
            width_px=DETAIL_MAX_TILE_WIDTH_PX,
        ),
        DetailTile(index=2, start_px=DETAIL_MAX_TILE_WIDTH_PX * 2, width_px=300),
    ]


def test_detail_tiles_covers_the_full_width_with_no_gaps_or_overlap() -> None:
    total = DETAIL_MAX_TILE_WIDTH_PX * 3 + 1

    tiles = detail_tiles(total_width_px=total)

    covered = 0
    for i, tile in enumerate(tiles):
        assert tile.index == i
        assert tile.start_px == covered
        covered += tile.width_px
    assert covered == total


def test_detail_params_includes_tiles_matching_the_spectrogram_width() -> None:
    # A duration long enough to need more than one tile at DETAIL_PX_PER_MS (~4.4138):
    # DETAIL_MAX_TILE_WIDTH_PX (8000) / 4.4138 / 1000 ~= 1.8125s per tile.
    params = detail_params(duration_s=2.0, samplerate_hz=256_000)

    covered = sum(tile.width_px for tile in params.tiles)
    assert covered == params.spectrogram.width_px
    assert len(params.tiles) > 1


def test_locked_scale_and_fft_constants_match_the_spec_decision() -> None:
    """Pins the values chosen by the 2026-09-02 scale/FFT spike (see the design spec's dated
    addendum) -- a future change to these constants should be a deliberate spec update, not an
    accidental edit that silently drifts from what's documented."""
    # Exact 1:1 with the real STFT hop at DETAIL_WINDOW_MS/DETAIL_OVERLAP for a 256kHz recording
    # (this project's EMT device rate) -- see DETAIL_PX_PER_MS's own derivation comment.
    assert DETAIL_PX_PER_MS == 256_000 / 58 / 1000
    assert DETAIL_PX_PER_KHZ == 4.7
    assert DETAIL_WINDOW_MS == 1.5
    assert DETAIL_OVERLAP == 0.85


def test_detail_params_spectrogram_uses_the_detail_only_fft_constants() -> None:
    """Detail-page-only (this task's ruling, see the plan) -- `detail_params()` must build its
    `SpectrogramParams` from `DETAIL_WINDOW_MS`/`DETAIL_OVERLAP`, not `SpectrogramParams`'s own
    class defaults, which the drawer/overview's cached pipeline uses instead. This is the guard
    against silently coupling the two back together in a future edit."""
    from fledermap.media.spectrogram import SpectrogramParams

    params = detail_params(duration_s=1.0, samplerate_hz=256_000)
    assert params.spectrogram.window_ms == DETAIL_WINDOW_MS
    assert params.spectrogram.overlap == DETAIL_OVERLAP
    # The shared class defaults must be untouched -- this is what keeps the drawer/overview
    # unaffected by this task.
    assert SpectrogramParams().window_ms == 3.0
    assert SpectrogramParams().overlap == 0.5
