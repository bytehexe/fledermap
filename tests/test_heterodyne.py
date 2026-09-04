from __future__ import annotations

import struct
from pathlib import Path

from fledermap.media.heterodyne import compute_peak_frequency_hz
from tests.fixtures import build_wav, fmt_payload


def _sine_wav(
    path: Path, *, freq_hz: float, samplerate: int = 256_000, duration_s: float = 0.05
) -> None:
    n_samples = int(samplerate * duration_s)
    pcm = struct.pack(
        f"<{n_samples}h",
        *(
            int(
                20000
                * __import__("math").sin(
                    2 * __import__("math").pi * freq_hz * i / samplerate
                )
            )
            for i in range(n_samples)
        ),
    )
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]),
    )


def test_compute_peak_frequency_hz_finds_a_known_single_tone(tmp_path: Path) -> None:
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=40_000.0)

    peak = compute_peak_frequency_hz(wav_path)

    # Welch's PSD has finite frequency resolution -- close, not exact.
    assert 38_000.0 < peak < 42_000.0


def _two_tone_wav(
    path: Path,
    *,
    loud_freq_hz: float,
    loud_amplitude: float,
    quiet_freq_hz: float,
    quiet_amplitude: float,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """Mixes two sine tones into ONE file (summed samples, not two separate
    writes -- writing `_sine_wav` twice at the same path would overwrite
    rather than mix)."""
    import math

    n_samples = int(samplerate * duration_s)
    samples = [
        int(
            loud_amplitude * math.sin(2 * math.pi * loud_freq_hz * i / samplerate)
            + quiet_amplitude * math.sin(2 * math.pi * quiet_freq_hz * i / samplerate)
        )
        for i in range(n_samples)
    ]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    path.write_bytes(build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]))


def test_compute_peak_frequency_hz_ignores_a_louder_tone_below_the_search_window(
    tmp_path: Path,
) -> None:
    """A real recording's low end (below ~10kHz) can carry handling/wind noise loud enough to
    dominate a raw argmax -- the bounded search window (spec §1) must reject it even when it's
    the objectively loudest component in the file. Mixes a quiet 40kHz tone (the "real call",
    in-window) with a much louder 2kHz tone (the "noise", below the window) into one file --
    without the window bound, the 2kHz tone's far greater amplitude would dominate a raw argmax
    and get reported as the peak instead."""
    wav_path = tmp_path / "tone.wav"
    _two_tone_wav(
        wav_path,
        loud_freq_hz=2_000.0,
        loud_amplitude=30_000.0,
        quiet_freq_hz=40_000.0,
        quiet_amplitude=3_000.0,
    )

    peak = compute_peak_frequency_hz(wav_path)

    assert 38_000.0 < peak < 42_000.0
