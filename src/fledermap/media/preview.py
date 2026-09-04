"""Time-expanded x10 preview generation. Pure: reads a WAV file, writes an
Opus file. No DB, no queue awareness (design spec §3).

`x10` (not resampling) matches design spec §5's "nearly free" framing exactly:
only the WAV header's declared frame rate changes, so a 45 kHz Pipistrellus
call lands at 4.5 kHz -- audible, classic time-expansion playback, no DSP.

Opus encoding shells out to `ffmpeg` (design spec §2, decision P3-2) rather
than a Python libopus binding -- one mature, well-known binary dependency
instead of a comparatively unmaintained Python wrapper plus manual container
muxing. The actual write/encode/atomic-replace pipeline lives in
`opus_pipeline.py`, shared with `heterodyne.py`
(2026-09-04-fledermap-het-playback-design.md section 1).
"""

from __future__ import annotations

import wave
from pathlib import Path

from fledermap.media.opus_pipeline import encode_pcm_as_opus

# Public: the recording details page's JS needs this to convert between the
# preview <audio>'s expanded playback clock and the spectrogram/oscillogram's
# native-real-time locked scale (audio.currentTime is on THIS expanded
# timeline, not the images' one) -- see web/views/recording_detail.py.
TIME_EXPANSION_FACTOR = 10


def make_preview(wav_path: Path, out_path: Path) -> None:
    """Render `wav_path`'s x10 time-expanded preview to `out_path` as Opus."""
    with wave.open(str(wav_path), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())

    slow_rate = params.framerate // TIME_EXPANSION_FACTOR
    encode_pcm_as_opus(
        frames=frames,
        nchannels=params.nchannels,
        sampwidth=params.sampwidth,
        framerate=slow_rate,
        out_path=out_path,
    )
