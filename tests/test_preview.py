from __future__ import annotations

import json
import math
import struct
import subprocess
from pathlib import Path

from fledermap.media.preview import make_preview


def _sine_wav(
    path: Path,
    *,
    freq_hz: float = 45_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
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


def _ffprobe_stream_info(path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    stream: dict[str, object] = data["streams"][0]
    return stream


def test_preview_duration_is_roughly_ten_times_the_source(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, samplerate=256_000, duration_s=0.05)
    out_path = tmp_path / "preview.opus"

    make_preview(wav_path, out_path)

    stream = _ffprobe_stream_info(out_path)
    # Opus always reports a 48000 Hz container rate regardless of the source
    # -- the actual pitch/speed change is encoded in the audio itself, not
    # exposed as a distinct sample-rate field. Assert on duration instead:
    # 0.05s of source audio at 1/10 speed must decode to roughly 0.5s.
    duration = float(stream["duration"])  # type: ignore[arg-type]
    assert 0.4 < duration < 0.6


def test_preview_output_is_a_real_nonempty_opus_file(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "preview.opus"

    make_preview(wav_path, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    stream = _ffprobe_stream_info(out_path)
    assert stream["codec_name"] == "opus"


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "preview.opus"

    make_preview(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p not in (wav_path, out_path)]
    assert leftover == []


def test_denoise_true_changes_preview_output(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(
        wav_path, freq_hz=2_000.0
    )  # below the 5kHz cutoff -- denoise should audibly change it
    plain_out = tmp_path / "plain.opus"
    denoised_out = tmp_path / "denoised.opus"

    make_preview(wav_path, plain_out, denoise=False)
    make_preview(wav_path, denoised_out, denoise=True)

    assert plain_out.read_bytes() != denoised_out.read_bytes()


def _decode_to_pcm(path: Path) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_denoise_false_is_byte_identical_to_no_kwarg(tmp_path: Path) -> None:
    """Regression guard: the default/off path must be untouched -- the
    existing raw-frame pass-through, not routed through decode/filter/
    re-encode at all.

    Compares decoded PCM samples rather than the two `.opus` files'
    raw bytes: ffmpeg's ogg muxer embeds a randomly-generated stream
    serial number on every invocation (confirmed by diffing two
    denoise=False encodes of identical input -- they differ starting
    exactly at the Ogg page header's serial-number field, while their
    decoded audio is bit-identical), so raw-byte equality can never hold
    across two separate `make_preview` calls regardless of correctness.
    """
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    explicit_out = tmp_path / "explicit.opus"
    default_out = tmp_path / "default.opus"

    make_preview(wav_path, explicit_out, denoise=False)
    make_preview(wav_path, default_out)

    assert _decode_to_pcm(explicit_out) == _decode_to_pcm(default_out)
