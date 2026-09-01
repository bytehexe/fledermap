from __future__ import annotations

from fledermap.services.recording_detail import (
    DETAIL_MAX_FREQ_KHZ,
    DETAIL_PX_PER_KHZ,
    DETAIL_PX_PER_MS,
    detail_params,
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
