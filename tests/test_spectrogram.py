from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy.signal import chirp

from fledermap.media.spectrogram import (
    SpectrogramParams,
    effective_max_freq_hz,
    render_full_spectrogram_image,
    render_spectrogram,
    stft_hop_samples,
)


def _sine_wav(
    path: Path,
    *,
    freq_hz: float = 45_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """A real, non-silent 16-bit mono PCM WAV -- a synthesized bat-call-range
    tone, not all-zero bytes, so the STFT has real structure to render."""
    n_samples = int(samplerate * duration_s)
    samples = [
        int(32000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
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
    out_path = tmp_path / "spectrogram.webp"
    params = SpectrogramParams(width_px=256, height_px=128)

    render_spectrogram(wav_path, out_path, params=params)

    with Image.open(out_path) as img:
        assert img.format == "WEBP"
        assert img.size == (256, 128)


def test_default_params_produce_the_default_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    with Image.open(out_path) as img:
        assert img.size == (
            SpectrogramParams().width_px,
            SpectrogramParams().height_px,
        )


def test_params_hash_changes_when_any_field_changes() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(width_px=base.width_px + 1)

    assert base.params_hash != changed.params_hash


def test_params_hash_is_stable_for_equal_params() -> None:
    a = SpectrogramParams(width_px=999)
    b = SpectrogramParams(width_px=999)

    assert a.params_hash == b.params_hash


def test_clamps_max_freq_to_the_recordings_own_nyquist_limit(tmp_path: Path) -> None:
    """A recording at 44.1 kHz (an ordinary, non-ultrasonic sample rate) has a
    Nyquist limit of 22.05 kHz -- far below the 128 kHz default. This must not
    crash or silently render garbage for the requested-but-nonexistent upper
    frequency range; it must render successfully, using its own real limit."""
    wav_path = tmp_path / "low_rate.wav"
    _sine_wav(wav_path, freq_hz=8_000.0, samplerate=44_100, duration_s=0.1)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise

    with Image.open(out_path) as img:
        assert img.size == (
            SpectrogramParams().width_px,
            SpectrogramParams().height_px,
        )


def test_a_very_short_recording_renders_without_warning(
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
) -> None:
    """32 samples at 256 kHz (0.125 ms) -- shorter than even one default
    3 ms analysis window. This is the exact shape of the CLI's own shared
    `_archive()` test fixture (tests/test_cli.py), which writes recordings
    this short. Without clamping nperseg to the signal's own length,
    scipy.signal.spectrogram silently shrinks it but raises a UserWarning
    doing so -- a defect under this project's pristine-test-output rule."""
    wav_path = tmp_path / "tiny.wav"
    _sine_wav(wav_path, samplerate=256_000, duration_s=32 / 256_000)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise

    assert len(recwarn) == 0, [str(w.message) for w in recwarn]


def test_renders_rgb_not_grayscale(tmp_path: Path) -> None:
    """The palette maps normalised power to colour, not a single grey
    channel -- a loud tone must produce a pixel with unequal R/G/B (e.g. the
    palette's yellow/red end), not a neutral grey triple."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    with Image.open(out_path) as img:
        assert img.mode == "RGB"
        pixels = np.asarray(img)
        # At least one pixel must be distinctly non-grey (the loud tone's
        # peak, coloured by the palette) -- proves colour mapping actually
        # happened rather than R==G==B everywhere (a grey image saved as RGB).
        spread = pixels.max(axis=-1).astype(int) - pixels.min(axis=-1).astype(int)
        assert spread.max() > 20


def test_quiet_background_is_not_washed_out_to_near_white(tmp_path: Path) -> None:
    """Regression test for the log1p bug: a signal with a loud transient
    tone against an otherwise-quiet background must render the quiet
    background near the palette's floor colour (black), not washed toward
    the bright end -- `log1p` on power values well below 1.0 barely
    distinguishes them from the loud tone once min-max normalised, so most
    of the image used to render either near-uniformly bright or with the
    entire noise floor crushed into a razor-thin band at the very bottom of
    the range. A proper dB-relative-to-peak scale with a fixed dynamic-range
    floor keeps the noise floor visibly dark."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, duration_s=0.2)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    with Image.open(out_path) as img:
        pixels = np.asarray(img).astype(int)
        brightness = pixels.sum(axis=-1)  # 0 (black) .. 765 (white)
        # The tone occupies a narrow frequency band; most of the image-time
        # rows are far from it and must render dark (near the palette floor).
        dark_fraction = (brightness < 100).mean()
        assert dark_fraction > 0.5, (
            f"only {dark_fraction:.0%} of pixels near-black -- background "
            "isn't staying near the palette floor"
        )


def test_silence_renders_solid_black(tmp_path: Path) -> None:
    """All-zero PCM must not divide by zero (peak power is 0) and must
    render as the palette's floor colour throughout."""
    wav_path = tmp_path / "silence.wav"
    channels, bits, samplerate = 1, 16, 256_000
    n_samples = int(samplerate * 0.05)
    pcm = b"\x00\x00" * n_samples
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH", 1, channels, samplerate, byte_rate, block_align, bits
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    wav_path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise (no div-by-zero)

    with Image.open(out_path) as img:
        pixels = np.asarray(img)
        assert pixels.max() == 0


def test_dynamic_range_db_participates_in_params_hash() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(dynamic_range_db=base.dynamic_range_db + 1)

    assert base.params_hash != changed.params_hash


def test_palette_participates_in_params_hash() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(palette="some_other_palette")

    assert base.params_hash != changed.params_hash


def test_effective_max_freq_hz_clamps_to_nyquist() -> None:
    assert effective_max_freq_hz(44_100, SpectrogramParams()) == 22_050.0


def test_effective_max_freq_hz_respects_the_params_ceiling() -> None:
    assert effective_max_freq_hz(256_000, SpectrogramParams()) == 128_000.0


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p != wav_path and p != out_path]
    assert leftover == []


def _two_tone_wav(
    path: Path,
    *,
    quiet_freq_hz: float = 20_000.0,
    quiet_amplitude: int = 2_000,
    loud_freq_hz: float = 80_000.0,
    loud_amplitude: int = 32_000,
    samplerate: int = 256_000,
    half_duration_s: float = 0.05,
) -> None:
    """A quiet tone for the first half, a much louder tone for the second half --
    `test_render_spectrogram_time_range_normalizes_to_the_whole_file_peak` needs a recording
    where the two halves have genuinely different loudness."""
    n_half = int(samplerate * half_duration_s)

    def _tone(freq_hz: float, amplitude: int) -> list[int]:
        return [
            int(amplitude * math.sin(2 * math.pi * freq_hz * i / samplerate))
            for i in range(n_half)
        ]

    samples = _tone(quiet_freq_hz, quiet_amplitude) + _tone(
        loud_freq_hz, loud_amplitude
    )
    pcm = struct.pack(f"<{len(samples)}h", *samples)

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


def test_render_full_spectrogram_image_is_independent_of_tile_dimensions(
    tmp_path: Path,
) -> None:
    """The extracted shared-computation step (render-cost optimization, v1 backlog: reused
    across every tile of one recording-detail page load, `web/views/media.py`) must not depend
    on `width_px`/`height_px` -- those only govern the FINAL per-tile resize, not the underlying
    STFT/palette image, or caching it per-tile would defeat the whole point."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)

    small = render_full_spectrogram_image(
        wav_path, SpectrogramParams(width_px=64, height_px=32)
    )
    large = render_full_spectrogram_image(
        wav_path, SpectrogramParams(width_px=4096, height_px=2048)
    )

    assert small.image.size == large.image.size


def test_render_spectrogram_accepts_a_precomputed_full_image(tmp_path: Path) -> None:
    """`render_spectrogram(..., full_image=...)` must produce pixel-identical output to a
    normal call -- the caching caller (`detail_spectrogram`) relies on this equivalence to skip
    recomputing the shared STFT/palette image across a page's tiles without changing what gets
    rendered."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    params = SpectrogramParams(width_px=64, height_px=32)

    normal_out = tmp_path / "normal.webp"
    render_spectrogram(wav_path, normal_out, params=params)

    full_image = render_full_spectrogram_image(wav_path, params)
    precomputed_out = tmp_path / "precomputed.webp"
    render_spectrogram(wav_path, precomputed_out, params=params, full_image=full_image)

    with (
        Image.open(normal_out) as normal_img,
        Image.open(precomputed_out) as precomputed_img,
    ):
        assert normal_img.size == precomputed_img.size
        assert normal_img.tobytes() == precomputed_img.tobytes()


def test_render_spectrogram_time_range_produces_the_requested_pixel_width(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, duration_s=0.1)
    out_path = tmp_path / "tile.webp"

    render_spectrogram(
        wav_path,
        out_path,
        params=SpectrogramParams(width_px=64, height_px=32),
        time_range_s=(0.0, 0.05),
    )

    image = Image.open(out_path)
    assert image.size == (64, 32)


def test_render_spectrogram_time_range_normalizes_to_the_whole_file_peak(
    tmp_path: Path,
) -> None:
    combined_path = tmp_path / "combined.wav"
    _two_tone_wav(combined_path)

    quiet_only_path = tmp_path / "quiet_only.wav"
    _sine_wav(quiet_only_path, freq_hz=20_000.0, duration_s=0.05)
    # _sine_wav's default amplitude (32000) differs from _two_tone_wav's quiet_amplitude (2000) --
    # build the quiet-only file directly with _two_tone_wav's own quiet parameters instead, so the
    # two renders share identical quiet-half content:
    _two_tone_wav(quiet_only_path, loud_amplitude=2_000, loud_freq_hz=20_000.0)
    # (quiet_only_path now has the SAME quiet tone in both halves -- i.e. its own peak equals the
    # quiet tone's own amplitude, unlike combined_path's peak, which is the loud second half.)

    sliced_out = tmp_path / "sliced.webp"
    render_spectrogram(
        combined_path,
        sliced_out,
        params=SpectrogramParams(width_px=50, height_px=50),
        time_range_s=(0.0, 0.05),
    )

    standalone_out = tmp_path / "standalone.webp"
    render_spectrogram(
        quiet_only_path,
        standalone_out,
        params=SpectrogramParams(width_px=50, height_px=50),
        time_range_s=(0.0, 0.05),
    )

    sliced_pixels = np.array(Image.open(sliced_out), dtype=np.float64)
    standalone_pixels = np.array(Image.open(standalone_out), dtype=np.float64)

    # Same quiet first-half content in both files, but `combined_path` has a much louder second
    # half -- its whole-file peak is much higher, so the SAME quiet content must render DIMMER
    # (lower mean brightness) when sliced from `combined_path` than when it's the loudest thing
    # in its own file. This is the whole point: normalization must use the WHOLE file's peak, not
    # the tile's own slice.
    assert sliced_pixels.mean() < standalone_pixels.mean()


def _chirp_wav(
    path: Path,
    *,
    f0_hz: float = 20_000.0,
    f1_hz: float = 100_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.1,
) -> None:
    """A linear frequency sweep -- unlike `_sine_wav`'s single stationary tone, this has real
    spectral structure that keeps changing continuously across a tile boundary, so a boundary
    artifact from two independently-resized tiles not agreeing has something to actually show up
    against (a stationary tone's spectrogram columns barely differ from each other at all, tile
    boundary or not)."""
    n = int(samplerate * duration_s)
    t = np.arange(n) / samplerate
    sig = 30_000 * chirp(t, f0=f0_hz, f1=f1_hz, t1=duration_s, method="linear")
    pcm = struct.pack(f"<{n}h", *sig.astype(np.int16).tolist())

    fmt_payload = struct.pack("<HHIIHH", 1, 1, samplerate, samplerate * 2, 2, 16)

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_render_spectrogram_tile_boundaries_dont_show_a_seam(tmp_path: Path) -> None:
    """Two adjacent tiles of the same recording sit edge-to-edge with no gap on the recording
    details page (services/recording_detail.py's `detail_tiles`) -- rendering each tile's own
    slice of STFT columns independently, resized up to its own pixel width, starved the
    resampling kernel of real pixels just past each tile's own edge (it fell back to clamping/
    replicating its own edge column instead), so the two tiles' resized edges didn't quite agree:
    a visible vertical seam at every tile boundary (confirmed against a real field recording
    2026-09-02). `render_spectrogram` now resizes the FULL, un-sliced image with a `box=` region
    instead, so the resampling kernel can see real neighbouring pixels just outside each tile's
    own edge, same as if the whole recording were rendered as one image and cropped afterward."""
    wav_path = tmp_path / "chirp.wav"
    _chirp_wav(wav_path)

    tile_a = tmp_path / "tile_a.webp"
    tile_b = tmp_path / "tile_b.webp"
    params = SpectrogramParams(width_px=200, height_px=64)
    render_spectrogram(wav_path, tile_a, params=params, time_range_s=(0.0, 0.05))
    render_spectrogram(wav_path, tile_b, params=params, time_range_s=(0.05, 0.1))

    a = np.array(Image.open(tile_a), dtype=np.float64)
    b = np.array(Image.open(tile_b), dtype=np.float64)

    # Tile A's rightmost column immediately precedes tile B's leftmost column in time -- they
    # should read as continuous, not as a jump. 4.0 sits between what this exact chirp measured
    # pre-fix (~5.5, independent per-tile resize) and post-fix (~2.6, box= on the full image).
    boundary_diff = np.abs(a[:, -1, :] - b[:, 0, :]).mean()
    assert boundary_diff < 4.0


def test_stft_hop_samples_matches_render_spectrogram_s_own_nperseg_noverlap_formula() -> (
    None
):
    """`stft_hop_samples` must compute the exact same hop `render_spectrogram` (this file) uses
    internally -- recomputed independently here from the same source formulas
    (`nperseg = int(samplerate_hz * window_ms / 1000)`, `noverlap = int(nperseg * overlap)`), not
    just re-asserting `stft_hop_samples`'s own implementation, so a future edit that changes one
    but not the other actually fails a test."""
    samplerate_hz, window_ms, overlap = 256_000, 1.5, 0.85
    nperseg = int(samplerate_hz * window_ms / 1000)
    noverlap = int(nperseg * overlap)
    expected_hop = nperseg - noverlap

    assert stft_hop_samples(samplerate_hz, window_ms, overlap) == expected_hop
    assert (
        expected_hop == 58
    )  # this project's EMT rate at the shipped detail-page FFT params


def test_stft_hop_samples_floors_nperseg_at_8() -> None:
    # A window so short at a low samplerate that samplerate_hz * window_ms / 1000 rounds under 8 --
    # the floor matters because a near-zero nperseg would make noverlap >= nperseg (a negative or
    # zero hop), same failure shape `render_spectrogram`'s own nperseg clamp exists to prevent.
    assert stft_hop_samples(samplerate_hz=8_000, window_ms=0.1, overlap=0.5) == 8 - int(
        8 * 0.5
    )
