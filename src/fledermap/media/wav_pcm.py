"""Reading 16-bit PCM WAV audio as a plain float array. Shared by
`spectrogram.py` and `oscillogram.py` -- both need the same raw samples, and
a second, drifted copy of this is exactly the kind of thing that goes stale
silently (see `media/paths.py`'s docstring on writer/reader formula
agreement for the general shape of that risk).
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


class UnreadableWavError(Exception):
    """Raised by `read_pcm` for a WAV file that exists on disk but can't be
    decoded as PCM audio -- a corrupt header, a file truncated (mid-sample or
    by a whole number of frames), or an unsupported bit depth. Callers that
    already 404 for a *missing* source file (recording detail
    page's tile routes) should treat this the same way rather than letting
    it surface as a raw 500: from a client's perspective "the file is there
    but unreadable" and "the file isn't there" are the same unusable state.
    """


def read_pcm(wav_path: Path) -> tuple[np.ndarray, int]:
    """Read mono or multi-channel 16-bit PCM as a 1-D float array (channels
    averaged down to mono) plus the file's own sample rate.

    Raises `UnreadableWavError` for a file that isn't a valid RIFF/WAVE
    container (`wave.open` itself raises `wave.Error`/`EOFError`), one
    truncated mid-sample -- `wave.readframes` silently returns however many
    bytes are actually on disk rather than raising, so a short read only
    surfaces once `np.frombuffer`/`reshape` see a byte count that doesn't
    evenly divide into samples (`ValueError`) -- or one that decodes
    "successfully" but is unusable downstream: a header-only file with no PCM
    data at all, a corrupt `fmt ` chunk claiming a zero sample rate, an
    unsupported bit depth (only 16-bit PCM -- this project's only real
    devices, EMT, are not known to write anything else, but a public release
    opens this up to other recorders), or a file truncated by an exact
    multiple of the frame size ("even-byte" truncation: the file ends
    cleanly between samples, so the mid-sample check above never fires, but
    fewer frames actually exist than the header's own `data` chunk claims --
    silently rendering a misleading image otherwise, the last real column
    stretched across the missing width instead of erroring). None of these
    four raise on their own, so they're checked explicitly after the decode
    rather than caught as an exception.
    """
    try:
        with wave.open(str(wav_path), "rb") as wav:
            n_channels = wav.getnchannels()
            samplerate = wav.getframerate()
            sampwidth = wav.getsampwidth()
            expected_frames = wav.getnframes()
            if sampwidth != 2:
                raise UnreadableWavError(
                    f"cannot read PCM from {wav_path}: unsupported sample "
                    f"width {sampwidth * 8}-bit (only 16-bit PCM is supported)"
                )
            raw = wav.readframes(expected_frames)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)
    except (wave.Error, EOFError, ValueError) as exc:
        raise UnreadableWavError(
            f"cannot read PCM from {wav_path}: {type(exc).__name__}: {exc}"
        ) from exc
    if samplerate <= 0:
        raise UnreadableWavError(
            f"cannot read PCM from {wav_path}: sample rate {samplerate}"
        )
    if samples.size == 0:
        raise UnreadableWavError(f"cannot read PCM from {wav_path}: no PCM data")
    # `samples.size` is already a per-FRAME count at this point regardless of channel count --
    # the multi-channel branch above reshaped+averaged (frames, n_channels) down to (frames,),
    # so no separate per-channel accounting is needed here.
    if samples.size < expected_frames:
        raise UnreadableWavError(
            f"cannot read PCM from {wav_path}: truncated -- header claims "
            f"{expected_frames} frames, only {samples.size} present"
        )
    return samples, samplerate
