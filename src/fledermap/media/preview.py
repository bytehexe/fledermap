"""Time-expanded x10 preview generation. Pure: reads a WAV file, writes an
Opus file. No DB, no queue awareness (design spec §3).

`x10` (not resampling) matches design spec §5's "nearly free" framing exactly:
only the WAV header's declared frame rate changes, so a 45 kHz Pipistrellus
call lands at 4.5 kHz -- audible, classic time-expansion playback, no DSP.

Opus encoding shells out to `ffmpeg` (design spec §2, decision P3-2) rather
than a Python libopus binding -- one mature, well-known binary dependency
instead of a comparatively unmaintained Python wrapper plus manual container
muxing.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path

# Public: the recording details page's JS needs this to convert between the
# preview <audio>'s expanded playback clock and the spectrogram/oscillogram's
# native-real-time locked scale (audio.currentTime is on THIS expanded
# timeline, not the images' one) -- see web/views/recording_detail.py.
TIME_EXPANSION_FACTOR = 10


def make_preview(wav_path: Path, out_path: Path) -> None:
    """Render `wav_path`'s x10 time-expanded preview to `out_path` as Opus."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(wav_path), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())

    slow_rate = params.framerate // TIME_EXPANSION_FACTOR

    tmp_wav_fd, tmp_wav_name = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_wav_fd)
    tmp_wav_path = Path(tmp_wav_name)
    try:
        with wave.open(str(tmp_wav_path), "wb") as relabelled:
            relabelled.setnchannels(params.nchannels)
            relabelled.setsampwidth(params.sampwidth)
            relabelled.setframerate(slow_rate)
            relabelled.writeframes(frames)

        out_fd, out_tmp_name = tempfile.mkstemp(
            dir=out_path.parent,
            suffix=".opus.tmp",
        )
        os.close(out_fd)
        out_tmp_path = Path(out_tmp_name)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(tmp_wav_path),
                    "-c:a",
                    "libopus",
                    "-f",
                    "opus",
                    str(out_tmp_path),
                ],
                check=True,
                capture_output=True,
            )
            os.replace(out_tmp_path, out_path)
        except BaseException:
            out_tmp_path.unlink(missing_ok=True)
            raise
    finally:
        tmp_wav_path.unlink(missing_ok=True)
