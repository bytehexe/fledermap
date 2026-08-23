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


WAMD_MODEL = 0x01
WAMD_APP_VERSION = 0x03
WAMD_DEVICE = 0x04
WAMD_TIMESTAMP = 0x05
WAMD_POSITION = 0x06
WAMD_AUTO_ID = 0x0B
WAMD_MANUAL_ID = 0x0C


def wamd_entry(type_id: int, text: str) -> bytes:
    body = text.encode("utf-8")
    return struct.pack("<HI", type_id, len(body)) + body


def wamd_payload(
    *,
    model: str | None = "Echo Meter Touch",
    app_version: str | None = "App 3.1.10",
    device: str | None = "iPhone Simulator",
    timestamp: str | None = "2015-06-10 09:54:54+0200",
    position: str | None = "WGS84,42.346973,-76.48760,(null)",
    auto_id: str | None = "EPTSER",
    manual_id: str | None = None,
) -> bytes:
    """Reproduces the layout observed in the real EMT sample files."""
    out = struct.pack("<HI", 0x00, 2) + struct.pack("<H", 1)
    for type_id, value in (
        (WAMD_MODEL, model),
        (WAMD_APP_VERSION, app_version),
        (WAMD_DEVICE, device),
        (WAMD_TIMESTAMP, timestamp),
        (WAMD_POSITION, position),
        (WAMD_AUTO_ID, auto_id),
        (WAMD_MANUAL_ID, manual_id),
    ):
        if value is not None:
            out += wamd_entry(type_id, value)
    return out
