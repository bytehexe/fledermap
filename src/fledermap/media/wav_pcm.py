"""Reading 16- or 24-bit signed PCM WAV audio as a plain float array. Shared by
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


def full_scale_amplitude(sampwidth: int) -> int:
    """Maximum representable magnitude for `sampwidth`-byte signed PCM (32767 for 16-bit) --
    the "clipped" threshold a sample hit its format's ceiling. Computed by formula rather than
    hardcoded at each call site, so a clipping check (`oscillogram.py`) never carries a
    bit-depth-specific magic number that would silently go stale if this module ever supports a
    bit depth other than 16."""
    return (1 << (sampwidth * 8 - 1)) - 1


def _decode_signed_pcm(raw: bytes, sampwidth: int) -> np.ndarray:
    """Decode `raw` as little-endian signed PCM of `sampwidth` bytes/sample into a 1-D
    float array. 16-bit has a native numpy dtype; 24-bit doesn't (there's no `int24`), so
    it's unpacked by hand: view as bytes, pad each 3-byte sample with a 4th sign-extension
    byte (0xFF if the top bit is set, 0x00 otherwise), then view the result as `int32`."""
    if sampwidth == 2:
        return np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    # sampwidth == 3 -- the only other width `read_pcm` accepts.
    as_bytes = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
    sign_extend = np.where(as_bytes[:, 2] >= 0x80, 0xFF, 0x00).astype(np.uint8)
    padded = np.column_stack([as_bytes, sign_extend])
    return padded.view(np.int32).reshape(-1).astype(np.float64)


def read_pcm(wav_path: Path) -> tuple[np.ndarray, int, int]:
    """Read mono or multi-channel 16- or 24-bit signed PCM as a 1-D float array
    (channels averaged down to mono) plus the file's own sample rate and full-scale
    amplitude (see `full_scale_amplitude`).

    Raises `UnreadableWavError` for a file that isn't a valid RIFF/WAVE
    container (`wave.open` itself raises `wave.Error`/`EOFError`), one
    truncated mid-sample -- `wave.readframes` silently returns however many
    bytes are actually on disk rather than raising, so a short read only
    surfaces once `np.frombuffer`/`reshape` see a byte count that doesn't
    evenly divide into samples (`ValueError`) -- or one that decodes
    "successfully" but is unusable downstream: a header-only file with no PCM
    data at all, a corrupt `fmt ` chunk claiming a zero sample rate, an
    unsupported bit depth (only 16- and 24-bit signed PCM -- the two widths
    real ultrasonic recorders (EMT and others) actually write; 8-bit PCM is
    unsigned per the WAV spec and would need a different decode path, and
    32-bit is ambiguous between integer and IEEE float), or a file truncated
    by an exact multiple of the frame size ("even-byte" truncation: the file
    ends cleanly between samples, so the mid-sample check above never fires,
    but fewer frames actually exist than the header's own `data` chunk claims
    -- silently rendering a misleading image otherwise, the last real column
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
            if sampwidth not in (2, 3):
                raise UnreadableWavError(
                    f"cannot read PCM from {wav_path}: unsupported sample "
                    f"width {sampwidth * 8}-bit (only 16- and 24-bit signed "
                    f"PCM is supported)"
                )
            raw = wav.readframes(expected_frames)
        samples = _decode_signed_pcm(raw, sampwidth)
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
    return samples, samplerate, full_scale_amplitude(sampwidth)
