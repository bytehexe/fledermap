from __future__ import annotations

import json
import math
import struct
import subprocess
from pathlib import Path

from fledermap.media.opus_pipeline import encode_pcm_as_opus


def _sine_frames(
    *, freq_hz: float = 1000.0, samplerate: int = 48_000, duration_s: float = 0.05
) -> bytes:
    n_samples = int(samplerate * duration_s)
    samples = [
        int(16000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{n_samples}h", *samples)


def _ffprobe_stream_info(path: Path) -> dict[str, object]:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    stream: dict[str, object] = data["streams"][0]
    return stream


def test_encode_pcm_as_opus_produces_a_real_nonempty_opus_file(tmp_path: Path) -> None:
    out_path = tmp_path / "out.opus"

    encode_pcm_as_opus(
        frames=_sine_frames(),
        nchannels=1,
        sampwidth=2,
        framerate=48_000,
        out_path=out_path,
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    stream = _ffprobe_stream_info(out_path)
    assert stream["codec_name"] == "opus"


def test_encode_pcm_as_opus_writes_atomically_leaving_no_temp_file_behind(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "out.opus"

    encode_pcm_as_opus(
        frames=_sine_frames(),
        nchannels=1,
        sampwidth=2,
        framerate=48_000,
        out_path=out_path,
    )

    leftover = [p for p in tmp_path.iterdir() if p != out_path]
    assert leftover == []
