"""Shared PCM->Opus encode pipeline: write a temp WAV at a given format, shell
out to `ffmpeg -c:a libopus`, atomically replace the output file. Extracted
from `preview.py` (design spec 2026-09-04-fledermap-het-playback-design.md
section 1) so `heterodyne.py` doesn't carry a second, driftable copy -- both
modules produce PCM frames by different means (a straight framerate
relabel for TE, real DSP for HET) but need the identical write/encode/replace
tail.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path


def encode_pcm_as_opus(
    *,
    frames: bytes,
    nchannels: int,
    sampwidth: int,
    framerate: int,
    out_path: Path,
) -> None:
    """Write `frames` as a temp WAV at (`nchannels`, `sampwidth`, `framerate`),
    encode it to Opus, and atomically replace `out_path`. Raises
    `subprocess.CalledProcessError` if `ffmpeg` fails."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_wav_fd, tmp_wav_name = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_wav_fd)
    tmp_wav_path = Path(tmp_wav_name)
    try:
        with wave.open(str(tmp_wav_path), "wb") as relabelled:
            relabelled.setnchannels(nchannels)
            relabelled.setsampwidth(sampwidth)
            relabelled.setframerate(framerate)
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
                    # The `ogg` muxer's `-page_duration` defaults to 1,000,000us -- a full
                    # second (confirmed via `ffmpeg -h muxer=ogg`) -- and Ogg seeking is
                    # page-granular, so seeking anywhere inside a page can force the decoder
                    # to start up to ~1s before the requested position. Every playback seek
                    # in this app (click-to-play, rewind, View Lock's floor/ceiling restarts)
                    # relies on landing close to where it asked for; the 1s default surfaced
                    # as audibly repeated content right after a seek (Janna, 2026-09-04, live
                    # use, HET mode). 20ms puts nearly every Opus frame on its own page.
                    "-page_duration",
                    "20000",
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
