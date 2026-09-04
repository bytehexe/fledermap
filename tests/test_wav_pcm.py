"""Plain unit tests for `read_pcm` -- no Flask app, no DB, no `Recording` row.
Deliberately not `db`-marked so this runs in the fast/pre-commit/CI path; the
route-level 404 behavior built on top of `UnreadableWavError` is covered
separately in `tests/test_media_view.py` (db-marked, since it needs the app +
DB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.media.wav_pcm import UnreadableWavError, read_pcm
from tests.fixtures import build_wav, fmt_payload


def _sine_pcm(*, n_samples: int = 100) -> bytes:
    import struct

    return struct.pack(f"<{n_samples}h", *([1000] * n_samples))


def test_read_pcm_rejects_a_non_riff_file(tmp_path: Path) -> None:
    path = tmp_path / "garbage.wav"
    path.write_bytes(b"not a wav file at all")

    with pytest.raises(UnreadableWavError):
        read_pcm(path)


def test_read_pcm_rejects_a_file_truncated_mid_sample(tmp_path: Path) -> None:
    wav_bytes = build_wav(
        [(b"fmt ", fmt_payload(256_000)), (b"data", _sine_pcm(n_samples=5000))],
    )
    path = tmp_path / "truncated.wav"
    # Drop an odd number of trailing bytes: the header still claims the original
    # data length, but the file ends mid-sample.
    path.write_bytes(wav_bytes[:-501])

    with pytest.raises(UnreadableWavError):
        read_pcm(path)


def test_read_pcm_rejects_a_multichannel_file_truncated_to_an_uneven_sample_count(
    tmp_path: Path,
) -> None:
    # 2 channels: a valid file needs an even total int16 sample count (each frame is
    # one sample per channel). Slice to an odd total sample count so it doesn't
    # divide evenly by n_channels.
    wav_bytes = build_wav(
        [
            (b"fmt ", fmt_payload(256_000, channels=2)),
            (b"data", _sine_pcm(n_samples=5000)),
        ],
    )
    path = tmp_path / "truncated_stereo.wav"
    # Drop 2 bytes (one int16 sample) so the total sample count is odd and can't
    # reshape into (-1, 2).
    path.write_bytes(wav_bytes[:-2])

    with pytest.raises(UnreadableWavError):
        read_pcm(path)


def test_read_pcm_rejects_a_header_only_file_with_no_pcm_data(tmp_path: Path) -> None:
    wav_bytes = build_wav([(b"fmt ", fmt_payload(256_000)), (b"data", b"")])
    path = tmp_path / "empty_data.wav"
    path.write_bytes(wav_bytes)

    with pytest.raises(UnreadableWavError):
        read_pcm(path)


def test_read_pcm_rejects_an_8_bit_file(tmp_path: Path) -> None:
    wav_bytes = build_wav(
        [
            (b"fmt ", fmt_payload(256_000, bits=8)),
            (b"data", bytes([128] * 5000)),
        ],
    )
    path = tmp_path / "eight_bit.wav"
    path.write_bytes(wav_bytes)

    with pytest.raises(UnreadableWavError):
        read_pcm(path)


def test_read_pcm_rejects_a_24_bit_file(tmp_path: Path) -> None:
    wav_bytes = build_wav(
        [
            (b"fmt ", fmt_payload(256_000, bits=24)),
            (b"data", bytes([0, 0, 1] * 5000)),
        ],
    )
    path = tmp_path / "twentyfour_bit.wav"
    path.write_bytes(wav_bytes)

    with pytest.raises(UnreadableWavError):
        read_pcm(path)


def test_read_pcm_rejects_a_file_truncated_by_a_whole_number_of_frames(
    tmp_path: Path,
) -> None:
    """The "even-byte" truncation case: the file ends cleanly on a sample boundary (so
    the mid-sample ValueError guard never fires), but the header's `data` chunk claims
    more frames than are actually present."""
    wav_bytes = build_wav(
        [(b"fmt ", fmt_payload(256_000)), (b"data", _sine_pcm(n_samples=5000))],
    )
    path = tmp_path / "truncated_whole_frames.wav"
    # Drop the last 100 samples (200 bytes) -- an exact multiple of the int16 frame
    # size, so it decodes "successfully" with no exception, just fewer samples than
    # the header's data-chunk length claims.
    path.write_bytes(wav_bytes[:-200])

    with pytest.raises(UnreadableWavError):
        read_pcm(path)


def test_read_pcm_returns_samples_and_samplerate_for_a_well_formed_file(
    tmp_path: Path,
) -> None:
    wav_bytes = build_wav(
        [(b"fmt ", fmt_payload(256_000)), (b"data", _sine_pcm(n_samples=5000))],
    )
    path = tmp_path / "good.wav"
    path.write_bytes(wav_bytes)

    samples, samplerate = read_pcm(path)

    assert samplerate == 256_000
    assert samples.size == 5000
