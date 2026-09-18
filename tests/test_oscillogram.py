from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fledermap.media.oscillogram import OscillogramParams, render_oscillogram
from tests.fixtures import build_wav, fmt_payload


def _sine_wav(
    path: Path,
    *,
    freq_hz: float = 45_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
    amplitude: int = 32000,
) -> None:
    import math

    n_samples = int(samplerate * duration_s)
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    pcm = struct.pack(f"<{n_samples}h", *samples)

    channels, bits = 1, 16
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH",
        1,
        channels,
        samplerate,
        byte_rate,
        block_align,
        bits,
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_renders_a_webp_of_the_configured_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "oscillogram.webp"
    params = OscillogramParams(width_px=200, height_px=60)

    render_oscillogram(wav_path, out_path, params=params)

    with Image.open(out_path) as img:
        assert img.format == "WEBP"
        assert img.size == (200, 60)


def test_default_params_produce_the_default_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)

    with Image.open(out_path) as img:
        assert img.size == (
            OscillogramParams().width_px,
            OscillogramParams().height_px,
        )


def test_amplitude_is_normalised_to_the_recordings_own_peak(tmp_path: Path) -> None:
    """A near-silent recording must still be VISIBLE -- normalised to its
    own peak, not a fixed int16-range reference, or a quiet call would
    render as a flat, invisible line even though it's the whole point of
    looking at this recording. A loud and a quiet (but non-silent) signal
    should therefore use a comparably wide vertical span, both close to the
    full available height."""
    loud_path = tmp_path / "loud.wav"
    quiet_path = tmp_path / "quiet.wav"
    _sine_wav(loud_path, amplitude=32000)
    _sine_wav(quiet_path, amplitude=500)

    loud_out = tmp_path / "loud.webp"
    quiet_out = tmp_path / "quiet.webp"
    params = OscillogramParams(height_px=48)
    render_oscillogram(loud_path, loud_out, params=params)
    render_oscillogram(quiet_path, quiet_out, params=params)

    def lit_row_span(path: Path) -> int:
        with Image.open(path) as img:
            pixels = np.asarray(img.convert("L"))
        # The line is drawn dark against a light background -- "lit" rows
        # are the ones containing at least one dark (line) pixel.
        lit_rows = np.where(pixels.min(axis=1) < 200)[0]
        return int(lit_rows.max() - lit_rows.min()) if lit_rows.size else 0

    # Both use most of the 48px height (a full-scale sine peaks at +-1, so
    # the theoretical max span is close to but under the full height).
    assert lit_row_span(loud_out) > 40
    assert lit_row_span(quiet_out) > 40


def test_silence_renders_a_flat_centre_line(tmp_path: Path) -> None:
    wav_path = tmp_path / "silence.wav"
    channels, bits, samplerate = 1, 16, 256_000
    n_samples = int(samplerate * 0.05)
    pcm = b"\x00\x00" * n_samples
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH",
        1,
        channels,
        samplerate,
        byte_rate,
        block_align,
        bits,
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    wav_path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)  # must not raise (no div-by-zero)

    with Image.open(out_path) as img:
        pixels = np.asarray(img.convert("L"))
        lit_rows = np.where(pixels.min(axis=1) < 200)[0]
        # Only the flat centre line itself may be lit, nothing above/below it.
        assert lit_rows.size > 0
        assert (lit_rows.max() - lit_rows.min()) <= 2


def test_params_hash_changes_when_any_field_changes() -> None:
    base = OscillogramParams()
    changed = OscillogramParams(width_px=base.width_px + 1)

    assert base.params_hash != changed.params_hash


def test_params_hash_is_stable_for_equal_params() -> None:
    a = OscillogramParams(width_px=999)
    b = OscillogramParams(width_px=999)

    assert a.params_hash == b.params_hash


def test_a_very_short_recording_renders_without_warning(
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
) -> None:
    wav_path = tmp_path / "tiny.wav"
    _sine_wav(wav_path, samplerate=256_000, duration_s=32 / 256_000)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)  # must not raise

    assert len(recwarn) == 0, [str(w.message) for w in recwarn]


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p != wav_path and p != out_path]
    assert leftover == []


def _constant_amplitude_wav(
    path: Path,
    *,
    amplitude: int,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """A flat-amplitude square-ish wave (alternating +amplitude/-amplitude) -- simpler than a
    sine tone and sufficient for testing peak-normalization width, not spectral content."""
    n_samples = int(samplerate * duration_s)
    samples = [amplitude if i % 2 == 0 else -amplitude for i in range(n_samples)]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    fmt = fmt_payload(samplerate)
    path.write_bytes(build_wav([(b"fmt ", fmt), (b"data", pcm)]))


def _two_amplitude_wav(
    path: Path,
    *,
    quiet_amplitude: int = 2_000,
    loud_amplitude: int = 32_000,
    samplerate: int = 256_000,
    half_duration_s: float = 0.05,
) -> None:
    n_half = int(samplerate * half_duration_s)
    quiet = [quiet_amplitude if i % 2 == 0 else -quiet_amplitude for i in range(n_half)]
    loud = [loud_amplitude if i % 2 == 0 else -loud_amplitude for i in range(n_half)]
    samples = quiet + loud
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    fmt = fmt_payload(samplerate)
    path.write_bytes(build_wav([(b"fmt ", fmt), (b"data", pcm)]))


def test_render_oscillogram_time_range_produces_the_requested_pixel_width(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "call.wav"
    _constant_amplitude_wav(wav_path, amplitude=20_000, duration_s=0.1)
    out_path = tmp_path / "tile.webp"

    render_oscillogram(
        wav_path,
        out_path,
        params=OscillogramParams(width_px=64, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    image = Image.open(out_path)
    assert image.size == (64, 20)


def test_render_oscillogram_time_range_normalizes_to_the_whole_file_peak(
    tmp_path: Path,
) -> None:
    combined_path = tmp_path / "combined.wav"
    _two_amplitude_wav(combined_path)

    quiet_only_path = tmp_path / "quiet_only.wav"
    _two_amplitude_wav(
        quiet_only_path, loud_amplitude=2_000
    )  # both halves quiet in this file

    sliced_out = tmp_path / "sliced.webp"
    render_oscillogram(
        combined_path,
        sliced_out,
        params=OscillogramParams(width_px=50, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    standalone_out = tmp_path / "standalone.webp"
    render_oscillogram(
        quiet_only_path,
        standalone_out,
        params=OscillogramParams(width_px=50, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    sliced_pixels = np.array(Image.open(sliced_out).convert("L"), dtype=np.float64)
    standalone_pixels = np.array(
        Image.open(standalone_out).convert("L"), dtype=np.float64
    )

    # Default line_color is black (0,0,0), background is white (255,255,255). A waveform
    # normalized to a much louder whole-file peak draws a SMALLER excursion from the midline --
    # more background (light) pixels remain, so mean brightness is HIGHER than the same quiet
    # content normalized to its own (equally quiet) peak.
    assert sliced_pixels.mean() > standalone_pixels.mean()


def test_params_hash_changes_when_cutoff_hz_changes() -> None:
    base = OscillogramParams()
    changed = OscillogramParams(cutoff_hz=base.cutoff_hz + 1)
    assert base.params_hash != changed.params_hash


def test_render_oscillogram_accepts_precomputed_samples(tmp_path: Path) -> None:
    """`render_oscillogram(..., samples=..., samplerate=...)` must produce a pixel-identical
    image to a normal call, and must use the GIVEN samples rather than re-reading and
    re-denoising the file -- the caching caller (`detail_oscillogram`, sharing one
    denoised-samples computation across a recording's tile requests, `web/views/media.py`)
    relies on this to skip the expensive highpass+spectral_gate pass on every tile."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    params = OscillogramParams(denoise=True)

    normal_out = tmp_path / "normal.webp"
    render_oscillogram(wav_path, normal_out, params=params)

    from fledermap.media.denoise import denoised_samples
    from fledermap.media.wav_pcm import read_pcm

    raw_samples, samplerate, full_scale = read_pcm(wav_path)
    precomputed_samples = denoised_samples(raw_samples, samplerate, params.cutoff_hz)
    # Deliberately pass a DIFFERENT source file -- if the function ignored the precomputed
    # samples and re-read from `wav_path`, the output could still match by coincidence, so
    # this proves it actually uses what was passed in, not the path.
    other_wav_path = tmp_path / "unrelated.wav"
    _sine_wav(other_wav_path, freq_hz=80_000.0, amplitude=1000)
    precomputed_out = tmp_path / "precomputed.webp"
    render_oscillogram(
        other_wav_path,
        precomputed_out,
        params=params,
        samples=precomputed_samples,
        samplerate=samplerate,
        full_scale=full_scale,
    )

    with (
        Image.open(normal_out) as normal_img,
        Image.open(precomputed_out) as precomputed_img,
    ):
        assert normal_img.tobytes() == precomputed_img.tobytes()


def test_render_oscillogram_accepts_precomputed_samples_and_full_scale(
    tmp_path: Path,
) -> None:
    """Companion to the plain precomputed-samples test above: `full_scale` must also be
    accepted and threaded through when `samples`/`samplerate` are given directly, the same
    "given together" contract."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "oscillogram.webp"

    from fledermap.media.wav_pcm import read_pcm

    samples, samplerate, full_scale = read_pcm(wav_path)

    render_oscillogram(
        wav_path,
        out_path,
        samples=samples,
        samplerate=samplerate,
        full_scale=full_scale,
    )  # must not raise


def _clipped_wav(
    path: Path,
    *,
    n_samples: int = 5000,
    clip_start: int,
    clip_end: int,
    samplerate: int = 256_000,
) -> None:
    """A recording whose samples are non-clipped (small) everywhere except one contiguous
    run alternating +-full-scale amplitude (32767) -- a synthetic worst-case clip, not a
    real detector artifact. Alternating sign (rather than a constant positive run) fills the
    bar's whole vertical span rather than a single-pixel-tall line, so the clip highlight
    survives lossy WebP compression instead of blurring away."""
    samples = [1000] * n_samples
    for i in range(clip_start, clip_end):
        samples[i] = 32767 if i % 2 == 0 else -32767
    pcm = struct.pack(f"<{n_samples}h", *samples)
    path.write_bytes(build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]))


def test_clipped_samples_render_in_clip_color(tmp_path: Path) -> None:
    wav_path = tmp_path / "clipped.wav"
    width = 100
    n_samples = 5000
    # Clip only the very last bucket's worth of samples, comfortably inside the last column.
    bucket_size = n_samples // width
    _clipped_wav(
        wav_path,
        n_samples=n_samples,
        clip_start=n_samples - bucket_size,
        clip_end=n_samples,
    )
    out_path = tmp_path / "oscillogram.webp"
    params = OscillogramParams(width_px=width, height_px=40, clip_color=(255, 0, 0))

    render_oscillogram(wav_path, out_path, params=params)

    with Image.open(out_path) as img:
        pixels = np.asarray(img.convert("RGB")).astype(np.int16)

    # Lossy WebP encoding blends a thin colored bar against its white background, so an exact
    # or near-exact color match isn't guaranteed -- check for a visibly RED pixel (R channel
    # well above G and B) rather than a tight distance from (255, 0, 0), matching this test
    # file's existing lossy-aware style (e.g. the `< 200` brightness threshold elsewhere).
    last_col = pixels[:, -1, :]
    reddish = (last_col[:, 0] - last_col[:, 1] > 80) & (
        last_col[:, 0] - last_col[:, 2] > 80
    )
    assert np.any(reddish)


def test_non_clipped_samples_do_not_render_in_clip_color(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, amplitude=1000)  # far below full scale, no clipping
    out_path = tmp_path / "oscillogram.webp"
    params = OscillogramParams(clip_color=(255, 0, 0))

    render_oscillogram(wav_path, out_path, params=params)

    with Image.open(out_path) as img:
        pixels = np.asarray(img.convert("RGB")).astype(np.int16)

    reddish = (pixels[..., 0] - pixels[..., 1] > 80) & (
        pixels[..., 0] - pixels[..., 2] > 80
    )
    assert not np.any(reddish)


def test_params_hash_changes_when_clip_color_changes() -> None:
    base = OscillogramParams()
    changed = OscillogramParams(clip_color=(1, 2, 3))
    assert base.params_hash != changed.params_hash


def test_params_hash_changes_when_denoise_changes() -> None:
    base = OscillogramParams()
    changed = OscillogramParams(denoise=not base.denoise)
    assert base.params_hash != changed.params_hash


def test_denoise_true_changes_oscillogram_pixels(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(
        wav_path, freq_hz=2_000.0
    )  # below the 5kHz cutoff -- should be visibly attenuated
    plain_out = tmp_path / "plain.webp"
    denoised_out = tmp_path / "denoised.webp"

    render_oscillogram(wav_path, plain_out, params=OscillogramParams(denoise=False))
    render_oscillogram(wav_path, denoised_out, params=OscillogramParams(denoise=True))

    with Image.open(plain_out) as a, Image.open(denoised_out) as b:
        assert list(a.get_flattened_data()) != list(b.get_flattened_data())


def test_denoise_reduces_envelope_outside_the_call(tmp_path: Path) -> None:
    """Distinguishes spectral gating from the highpass filter alone: the
    peak-envelope amplitude in a quiet region, well above the 5kHz highpass
    cutoff, must shrink with denoise=True vs denoise=False."""
    wav_path = tmp_path / "call.wav"
    samplerate = 256_000
    duration_s = 0.05
    n = int(samplerate * duration_s)
    t = np.arange(n) / samplerate
    pulse_start, pulse_end = int(n * 0.4), int(n * 0.6)
    rng = np.random.default_rng(1)
    clean = np.zeros(n)
    clean[pulse_start:pulse_end] = 20000 * np.sin(
        2 * np.pi * 45_000 * t[pulse_start:pulse_end]
    )
    noise = rng.normal(0, 3000, n)
    samples = (clean + noise).astype(np.int16)

    wav_path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload(samplerate)),
                (b"data", samples.tobytes()),
            ]
        )
    )

    plain_out = tmp_path / "plain.webp"
    denoised_out = tmp_path / "denoised.webp"
    render_oscillogram(wav_path, plain_out, params=OscillogramParams(denoise=False))
    render_oscillogram(wav_path, denoised_out, params=OscillogramParams(denoise=True))

    with Image.open(plain_out) as plain_img, Image.open(denoised_out) as denoised_img:
        # Count non-background pixels over a whole quiet region (columns
        # before the pulse, which starts at 40% width), not a single column --
        # a single column's envelope height is noisy enough to go either way
        # even when the region as a whole is clearly quieter (spiked
        # 2026-09-14: a per-column version of this assertion was flaky the
        # same way a single-pixel spectrogram check was).
        plain_arr = np.array(plain_img)
        denoised_arr = np.array(denoised_img)
        background = np.array(OscillogramParams().background_color)
        col_end = int(0.3 * plain_arr.shape[1])

        def _nonbackground_pixel_count(arr: np.ndarray) -> int:
            region = arr[:, :col_end]
            return int((~np.all(region == background, axis=-1)).sum())

        plain_count = _nonbackground_pixel_count(plain_arr)
        denoised_count = _nonbackground_pixel_count(denoised_arr)
        assert denoised_count < plain_count * 0.85
