"""Builders for synthetic WAV files carrying exactly the chunks a test needs."""

from __future__ import annotations

import struct


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    """One RIFF chunk: id, little-endian size, payload, pad to even length."""
    out = chunk_id + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        out += b"\x00"
    return out


def fmt_payload(samplerate: int = 256000, channels: int = 1, bits: int = 16) -> bytes:
    """A canonical PCM `fmt ` payload matching what the EMT writes."""
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    return struct.pack(
        "<HHIIHH",
        1,
        channels,
        samplerate,
        byte_rate,
        block_align,
        bits,
    )


def build_wav(chunks: list[tuple[bytes, bytes]]) -> bytes:
    """Assemble a RIFF/WAVE file from (chunk_id, payload) pairs, in order."""
    body = b"WAVE" + b"".join(_chunk(cid, payload) for cid, payload in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def minimal_wav(audio: bytes = b"\x01\x00\x02\x00", samplerate: int = 256000) -> bytes:
    """The smallest file the parser should accept: fmt + data."""
    return build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", audio)])
