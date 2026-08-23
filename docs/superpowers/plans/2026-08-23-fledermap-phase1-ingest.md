# Fledermap Phase 1 — Ingest Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `fledermap ingest <dir>` walks a directory of Echo Meter Touch recordings and populates `bats_db` idempotently, deriving nothing and serving nothing.

**Architecture:** A pure, side-effect-free scanning library (`ingest/`) emits `ScannedFile` records; a service layer (`services/ingest.py`) resolves each against the database by `audio_hash` and writes rows. Identity is a hash over the audio payload only, so renames and metadata rewrites are updates rather than duplicates. Nothing in this phase touches HTTP, media rendering, or clustering.

**Tech Stack:** Python ≥3.11 · hatch · SQLAlchemy 2.0 + GeoAlchemy2 + Alembic · PostgreSQL/PostGIS · `guano` 1.0.16 · click · pytest + testcontainers

**Spec:** `docs/superpowers/specs/2026-08-23-fledermap-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Python ≥ 3.11.** `requires-python = ">=3.11"`.
- **hatch only.** Never `pip`, never a manual venv, never `PYTHONPATH`, never bare `python`/`python3`. Run things as `hatch test`, `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run fledermap ...`.
- **Licence: MIT.** Already at `LICENSE.txt`.
- **Conventional Commits**, enforced by gitlint. Prefixes used here: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.
- **Commits are GPG-signed** — `commit.gpgsign` is already `true` repo-locally. gpg-agent cannot write inside the command sandbox, so **always commit with the sandbox disabled**. Never use `--no-gpg-sign`.
- **Stage explicit paths.** Never `git add -A` or `git add .`.
- **Ruff config:** `select = ["E","W","F","I","UP","B","A","COM","ANN"]`, `ignore = ["E501","COM812","ANN401"]`, double quotes, `extend-exclude = ["docs/"]`.
- **Run `hatch run ruff:ruff format .` (writing) before `hatch run ruff:ruff format --check .` (verifying).** The code blocks in this plan are written for readability and carry magic trailing commas on single-line calls; `skip-magic-trailing-comma = false` expands them. Transcribing verbatim and going straight to `--check` fails. Format first, then check.
- **`bats_db` is never `poiidx_db`.** poiidx drops and recreates its tables on any config change. Pointing Fledermap at it destroys data. This must be a comment at the connection site, not only in the spec.
- **Ingest is strictly read-only on `archive_root`.** It never moves, renames, or deletes a source file.
- **`recorded_at` default is provisional.** `timestamp_source: filename` is a config default flagged provisional (spec D17), not a settled decision. Do not remove the `metadata_at` column or the disagreement flag as "unused".

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.pre-commit-config.yaml`, `.gitlint`, `.yamllint`
- Create: `src/fledermap/__init__.py`, `src/fledermap/py.typed`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing
- Produces: an importable `fledermap` package; `hatch test`, `hatch run types:check`, `hatch run ruff:ruff check .` all green

- [ ] **Step 1: Write the failing test**

`tests/test_smoke.py`:

```python
def test_package_imports() -> None:
    import fledermap

    assert fledermap.__name__ == "fledermap"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap'` (or hatch errors because there is no `pyproject.toml` yet).

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "fledermap"
dynamic = ["version"]
description = "Map-first organiser for bat recordings from handheld detectors"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
authors = [{ name = "Janna Hopp" }]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Programming Language :: Python",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
]
dependencies = [
  "sqlalchemy>=2.0",
  "geoalchemy2",
  "alembic",
  "psycopg2-binary",
  "guano>=1.0.16",
  "click",
  "platformdirs",
  "pyyaml",
]

[project.scripts]
fledermap = "fledermap.cli.main:cli"

[tool.hatch.version]
path = "src/fledermap/__about__.py"
source = "vcs"

[tool.hatch.version.vcs]
fallback-version = "0.0.0"

[tool.hatch.version.raw-options]
local_scheme = "no-local-version"

[tool.hatch.build.hooks.vcs]
version-file = "src/fledermap/__about__.py"

[tool.hatch.envs.hatch-test]
extra-dependencies = [
  "pytest>=7.0.0",
  "testcontainers",
]

[tool.hatch.envs.types]
# pytest ships inline types; without it here mypy cannot resolve `import pytest`
# in the test files, and the tempting fix — a global ignore_missing_imports —
# would blind-spot every third-party import in the project.
extra-dependencies = ["mypy>=1.0.0", "pytest>=7.0.0"]

[tool.hatch.envs.types.scripts]
check = "mypy --install-types --non-interactive {args:src/fledermap tests}"

[tool.hatch.envs.ruff]
skip-install = true
detached = true
extra-dependencies = ["ruff"]

[tool.pytest.ini_options]
filterwarnings = [
  "ignore::DeprecationWarning:testcontainers.*",
]
markers = [
  "db: requires a PostGIS container",
]

[tool.ruff]
# Docs are prose deliverables. Ruff formats Python code fences inside markdown,
# which would rewrite the committed spec and plan documents.
extend-exclude = ["docs/"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "B", "A", "COM", "ANN"]
ignore = ["E501", "COM812", "ANN401"]
fixable = ["ALL"]
unfixable = []

[tool.ruff.lint.flake8-annotations]
mypy-init-return = true

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "auto"

[tool.coverage.run]
source_pkgs = ["fledermap", "tests"]
branch = true
parallel = true
omit = ["src/fledermap/__about__.py"]
```

- [ ] **Step 4: Create the package skeleton and supporting config**

`src/fledermap/__init__.py` — empty file.
`src/fledermap/py.typed` — empty file.

`README.md`:

```markdown
# fledermap

Map-first organiser for bat recordings from handheld detectors.

Design: `docs/superpowers/specs/2026-08-23-fledermap-design.md`
```

`.gitlint`:

```ini
[general]
contrib=contrib-title-conventional-commits
```

`.yamllint`:

```yaml
extends: default
rules:
  line-length: disable
  document-start: disable
```

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/adrienverge/yamllint
    rev: v1.35.1
    hooks:
      - id: yamllint
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: mypy
        name: mypy
        entry: hatch run types:check
        language: system
        pass_filenames: false
      - id: tests
        name: tests
        entry: hatch test
        language: system
        pass_filenames: false
  - repo: https://github.com/jorisroovers/gitlint
    rev: v0.19.1
    hooks:
      - id: gitlint
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_smoke.py -v`
Expected: PASS.

Then: `hatch run ruff:ruff check .` → no errors. `hatch run types:check` → `Success: no issues found`.

- [ ] **Step 6: Commit**

Run with the sandbox disabled:

```bash
git add pyproject.toml README.md .gitlint .yamllint .pre-commit-config.yaml \
        src/fledermap/__init__.py src/fledermap/py.typed tests/test_smoke.py
git commit -m "chore: scaffold fledermap package with hatch"
```

---

### Task 2: RIFF chunk walker

The WAV container is `"RIFF" · uint32 size · "WAVE"` followed by chunks of `id(4) · uint32 size · payload`, each padded to an even byte count. Everything downstream needs chunk offsets, so this comes first.

**Files:**
- Create: `src/fledermap/ingest/__init__.py`, `src/fledermap/ingest/riff.py`
- Create: `tests/fixtures.py`
- Test: `tests/test_riff.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Chunk` — frozen dataclass, fields `chunk_id: str`, `offset: int` (byte offset of the payload, *not* the header), `size: int`
  - `iter_chunks(path: Path) -> Iterator[Chunk]`
  - `read_chunk(path: Path, chunk: Chunk) -> bytes`
  - `NotARiffFileError(Exception)`
  - `tests.fixtures.build_wav(chunks: list[tuple[bytes, bytes]]) -> bytes` — samplerate belongs to `fmt_payload`, not here

- [ ] **Step 1: Write the fixture builder**

`tests/fixtures.py`. Every later task depends on this — it is how synthesised WAVs get made, so no real audio ever enters git.

```python
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
        "<HHIIHH", 1, channels, samplerate, byte_rate, block_align, bits,
    )


def build_wav(chunks: list[tuple[bytes, bytes]]) -> bytes:
    """Assemble a RIFF/WAVE file from (chunk_id, payload) pairs, in order."""
    body = b"WAVE" + b"".join(_chunk(cid, payload) for cid, payload in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def minimal_wav(audio: bytes = b"\x01\x00\x02\x00", samplerate: int = 256000) -> bytes:
    """The smallest file the parser should accept: fmt + data."""
    return build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", audio)])
```

- [ ] **Step 2: Write the failing test**

`tests/test_riff.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.ingest.riff import NotARiffFileError, iter_chunks, read_chunk
from tests.fixtures import build_wav, fmt_payload, minimal_wav


def test_iter_chunks_finds_fmt_and_data(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    path.write_bytes(minimal_wav(audio=b"\x01\x00\x02\x00"))

    chunks = {c.chunk_id: c for c in iter_chunks(path)}

    assert set(chunks) == {"fmt ", "data"}
    assert chunks["data"].size == 4


def test_iter_chunks_reads_trailing_wamd(tmp_path: Path) -> None:
    """The real EMT samples put `wamd` after `data`, at the end of the file."""
    path = tmp_path / "b.wav"
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload()),
                (b"data", b"\x00" * 10),
                (b"wamd", b"\x01\x02\x03"),
            ],
        ),
    )

    assert [c.chunk_id for c in iter_chunks(path)] == ["fmt ", "data", "wamd"]


def test_odd_sized_chunk_is_padded(tmp_path: Path) -> None:
    """A 3-byte chunk is followed by a pad byte; the next chunk must still be found."""
    path = tmp_path / "c.wav"
    path.write_bytes(
        build_wav(
            [(b"fmt ", fmt_payload()), (b"guan", b"abc"), (b"data", b"\x00\x00")],
        ),
    )

    assert [c.chunk_id for c in iter_chunks(path)] == ["fmt ", "guan", "data"]


def test_read_chunk_returns_payload(tmp_path: Path) -> None:
    path = tmp_path / "d.wav"
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload()), (b"data", b"\xde\xad\xbe\xef")]),
    )

    data = next(c for c in iter_chunks(path) if c.chunk_id == "data")

    assert read_chunk(path, data) == b"\xde\xad\xbe\xef"


def test_non_riff_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "not.wav"
    path.write_bytes(b"this is not a RIFF file at all")

    with pytest.raises(NotARiffFileError):
        list(iter_chunks(path))
```

- [ ] **Step 3: Run it to verify it fails**

Run: `hatch test tests/test_riff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.ingest'`.

- [ ] **Step 4: Implement**

`src/fledermap/ingest/__init__.py` — empty file.

`src/fledermap/ingest/riff.py`:

```python
"""Streaming RIFF/WAVE chunk parsing.

Never loads a whole file into memory: recordings run to hundreds of megabytes.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_HEADER = 8


class NotARiffFileError(Exception):
    """The file is not a RIFF/WAVE container."""


@dataclass(frozen=True)
class Chunk:
    """One RIFF sub-chunk. `offset` points at the payload, not the header."""

    chunk_id: str
    offset: int
    size: int


def iter_chunks(path: Path) -> Iterator[Chunk]:
    """Yield every sub-chunk in file order."""
    with path.open("rb") as fh:
        header = fh.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            msg = f"{path} is not a RIFF/WAVE file"
            raise NotARiffFileError(msg)

        while True:
            raw = fh.read(_HEADER)
            if len(raw) < _HEADER:
                return
            chunk_id = raw[0:4].decode("ascii", errors="replace")
            (size,) = struct.unpack("<I", raw[4:8])
            yield Chunk(chunk_id=chunk_id, offset=fh.tell(), size=size)
            fh.seek(size + (size % 2), 1)


def read_chunk(path: Path, chunk: Chunk) -> bytes:
    """Read one chunk's payload."""
    with path.open("rb") as fh:
        fh.seek(chunk.offset)
        return fh.read(chunk.size)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_riff.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/ingest/__init__.py src/fledermap/ingest/riff.py \
        tests/fixtures.py tests/test_riff.py
git commit -m "feat: add streaming RIFF chunk walker"
```

---

### Task 3: `audio_hash` — identity that survives re-ID

This is the load-bearing property of the whole design (spec D8). The EMT renames files when auto-ID runs, and its metadata lives *inside* the RIFF container, so neither the path nor a whole-file hash is stable. Hashing `fmt ‖ data` is.

**Files:**
- Modify: `src/fledermap/ingest/riff.py`
- Test: `tests/test_audio_hash.py`

**Interfaces:**
- Consumes: `iter_chunks`, `Chunk` from Task 2
- Produces: `audio_hash(path: Path) -> str` — 64-char lowercase hex sha256; `MissingAudioChunkError(Exception)`; `read_format(path: Path) -> AudioFormat`; `AudioFormat` — frozen dataclass `samplerate_hz: int`, `channels: int`, `bits: int`, `duration_s: float`

- [ ] **Step 1: Write the failing test**

`tests/test_audio_hash.py`. The second test is the one that matters — it is spec D8 tested directly.

```python
from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.ingest.riff import MissingAudioChunkError, audio_hash
from tests.fixtures import build_wav, fmt_payload


def _write(path: Path, chunks: list[tuple[bytes, bytes]]) -> Path:
    path.write_bytes(build_wav(chunks))
    return path


def test_hash_is_stable_and_hex(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload()), (b"data", b"\x01\x02\x03\x04")],
    )

    digest = audio_hash(path)

    assert len(digest) == 64
    assert digest == audio_hash(path)


def test_metadata_change_does_not_change_hash(tmp_path: Path) -> None:
    """THE load-bearing test: re-ID rewrites metadata, identity must survive."""
    audio = b"\x11\x22\x33\x44" * 64
    before = _write(
        tmp_path / "NoID_20260821_214532.wav",
        [
            (b"fmt ", fmt_payload()),
            (b"data", audio),
            (b"guan", b"GUANO|Version: 1.0\nSpecies Auto ID: \n"),
        ],
    )
    after = _write(
        tmp_path / "PIPPIP_20260821_214532.wav",
        [
            (b"fmt ", fmt_payload()),
            (b"data", audio),
            (b"guan", b"GUANO|Version: 1.0\nSpecies Auto ID: PIPPIP\nNote: re-run\n"),
        ],
    )

    assert audio_hash(before) == audio_hash(after)


def test_chunk_order_does_not_change_hash(tmp_path: Path) -> None:
    """GUANO may sit anywhere in the container; ordering must not matter."""
    audio = b"\xaa\xbb" * 32
    a = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload()), (b"data", audio), (b"guan", b"x")],
    )
    b = _write(
        tmp_path / "b.wav",
        [(b"fmt ", fmt_payload()), (b"guan", b"x"), (b"data", audio)],
    )

    assert audio_hash(a) == audio_hash(b)


def test_different_audio_changes_hash(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.wav", [(b"fmt ", fmt_payload()), (b"data", b"\x01\x02")])
    b = _write(tmp_path / "b.wav", [(b"fmt ", fmt_payload()), (b"data", b"\x03\x04")])

    assert audio_hash(a) != audio_hash(b)


def test_different_samplerate_changes_hash(tmp_path: Path) -> None:
    """`fmt ` is hashed too, so identical payloads at different rates differ."""
    audio = b"\x01\x02\x03\x04"
    a = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload(samplerate=256000)), (b"data", audio)],
    )
    b = _write(
        tmp_path / "b.wav",
        [(b"fmt ", fmt_payload(samplerate=384000)), (b"data", audio)],
    )

    assert audio_hash(a) != audio_hash(b)


def test_missing_data_chunk_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.wav", [(b"fmt ", fmt_payload())])

    with pytest.raises(MissingAudioChunkError):
        audio_hash(path)


def test_read_format_reports_rate_and_duration(tmp_path: Path) -> None:
    """One second of 256 kHz 16-bit mono is 512000 bytes of `data`."""
    path = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload(samplerate=256000)), (b"data", b"\x00" * 512000)],
    )

    fmt = read_format(path)

    assert fmt.samplerate_hz == 256000
    assert fmt.channels == 1
    assert fmt.bits == 16
    assert fmt.duration_s == pytest.approx(1.0)
```

Update the import line at the top of this file to:

```python
from fledermap.ingest.riff import (
    MissingAudioChunkError,
    audio_hash,
    read_format,
)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_audio_hash.py -v`
Expected: FAIL — `ImportError: cannot import name 'audio_hash'`.

- [ ] **Step 3: Implement**

Append to `src/fledermap/ingest/riff.py`:

```python
import hashlib

_BLOCK = 1024 * 1024


class MissingAudioChunkError(Exception):
    """The file lacks a `fmt ` or `data` chunk and cannot be identified."""


def audio_hash(path: Path) -> str:
    """Identity of a recording: sha256 over the `fmt ` and `data` payloads only.

    Deliberately excludes every metadata chunk. The Echo Meter Touch renames
    files and rewrites its metadata when auto-ID is re-run; hashing the audio
    payload means that is recognised as the *same* recording rather than a
    duplicate. See spec D8.
    """
    chunks = {c.chunk_id: c for c in iter_chunks(path)}
    try:
        fmt_chunk, data_chunk = chunks["fmt "], chunks["data"]
    except KeyError as exc:
        msg = f"{path} has no {exc.args[0]!r} chunk"
        raise MissingAudioChunkError(msg) from exc

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        fh.seek(fmt_chunk.offset)
        digest.update(fh.read(fmt_chunk.size))

        fh.seek(data_chunk.offset)
        remaining = data_chunk.size
        while remaining > 0:
            block = fh.read(min(_BLOCK, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)

    return digest.hexdigest()


@dataclass(frozen=True)
class AudioFormat:
    """PCM parameters plus the duration implied by the `data` chunk size."""

    samplerate_hz: int
    channels: int
    bits: int
    duration_s: float


def read_format(path: Path) -> AudioFormat:
    """Read `fmt ` and derive duration from the `data` chunk size.

    Duration comes from byte counts rather than any metadata field, so it is
    correct even when the detector writes none.
    """
    chunks = {c.chunk_id: c for c in iter_chunks(path)}
    try:
        fmt_chunk, data_chunk = chunks["fmt "], chunks["data"]
    except KeyError as exc:
        msg = f"{path} has no {exc.args[0]!r} chunk"
        raise MissingAudioChunkError(msg) from exc

    payload = read_chunk(path, fmt_chunk)
    _, channels, samplerate, byte_rate, _, bits = struct.unpack_from("<HHIIHH", payload)
    duration = data_chunk.size / byte_rate if byte_rate else 0.0
    return AudioFormat(
        samplerate_hz=samplerate,
        channels=channels,
        bits=bits,
        duration_s=duration,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_audio_hash.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/ingest/riff.py tests/test_audio_hash.py
git commit -m "feat: identify recordings by hash over audio chunks only"
```

---

### Task 4: `wamd` reader

The only metadata the real sample files carry. Structure decoded from actual bytes during the phase-0 spike (spec §11 R1): repeated entries of `uint16 type · uint32 size · payload`.

**Files:**
- Create: `src/fledermap/ingest/wamd.py`
- Modify: `tests/fixtures.py`
- Test: `tests/test_wamd.py`

**Interfaces:**
- Consumes: `read_chunk`, `Chunk` from Task 2
- Produces:
  - `WamdMetadata` — frozen dataclass: `model: str | None`, `app_version: str | None`, `device: str | None`, `timestamp: datetime | None`, `latitude: float | None`, `longitude: float | None`, `elevation_m: float | None`, `auto_id: str | None`, `manual_id: str | None`
  - `parse_wamd(payload: bytes) -> WamdMetadata`
  - `tests.fixtures.wamd_payload(**fields) -> bytes`

- [ ] **Step 1: Add the fixture builder**

Append to `tests/fixtures.py`:

```python
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
```

- [ ] **Step 2: Write the failing test**

`tests/test_wamd.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fledermap.ingest.wamd import parse_wamd
from tests.fixtures import wamd_payload


def test_parses_the_observed_sample_layout() -> None:
    """Byte-for-byte the shape of EPTSER_20150610_215446.wav's wamd chunk."""
    meta = parse_wamd(wamd_payload())

    assert meta.model == "Echo Meter Touch"
    assert meta.app_version == "App 3.1.10"
    assert meta.device == "iPhone Simulator"
    assert meta.timestamp == datetime(
        2015, 6, 10, 9, 54, 54, tzinfo=timezone(timedelta(hours=2)),
    )
    assert meta.latitude == 42.346973
    assert meta.longitude == -76.48760
    assert meta.auto_id == "EPTSER"


def test_null_elevation_becomes_none() -> None:
    """The EMT writes the literal string '(null)', not an empty field."""
    assert parse_wamd(wamd_payload()).elevation_m is None


def test_real_elevation_is_parsed() -> None:
    meta = parse_wamd(wamd_payload(position="WGS84,52.5194,13.4012,34.5"))

    assert meta.elevation_m == 34.5


def test_auto_and_manual_id_are_separate() -> None:
    """MYODAU_20150623_213547.wav carries both; they must not collapse."""
    meta = parse_wamd(wamd_payload(auto_id="MYODAU", manual_id="MYODAU"))

    assert meta.auto_id == "MYODAU"
    assert meta.manual_id == "MYODAU"


def test_absent_fields_are_none() -> None:
    meta = parse_wamd(wamd_payload(position=None, auto_id=None))

    assert meta.latitude is None
    assert meta.longitude is None
    assert meta.auto_id is None
    assert meta.manual_id is None


def test_unknown_entry_types_are_skipped() -> None:
    """Forward compatibility: a firmware update adding a field must not break ingest."""
    from tests.fixtures import wamd_entry

    payload = wamd_payload() + wamd_entry(0x7F, "something new")

    assert parse_wamd(payload).model == "Echo Meter Touch"


def test_truncated_payload_does_not_raise() -> None:
    truncated = wamd_payload()[:20]

    parse_wamd(truncated)  # must not raise


def test_malformed_position_is_ignored() -> None:
    meta = parse_wamd(wamd_payload(position="garbage"))

    assert meta.latitude is None
```

- [ ] **Step 3: Run it to verify it fails**

Run: `hatch test tests/test_wamd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.ingest.wamd'`.

- [ ] **Step 4: Implement**

`src/fledermap/ingest/wamd.py`:

```python
"""Reader for Wildlife Acoustics' proprietary `wamd` metadata chunk.

Structure decoded from real Echo Meter Touch sample files during the phase-0
spike (spec section 11, R1): repeated entries of

    uint16 type_id · uint32 size · payload[size]

Type IDs are those observed. Unknown types are skipped rather than treated as
errors, so a firmware update that adds a field does not break ingest.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime

_TYPE_MODEL = 0x01
_TYPE_APP_VERSION = 0x03
_TYPE_DEVICE = 0x04
_TYPE_TIMESTAMP = 0x05
_TYPE_POSITION = 0x06
_TYPE_AUTO_ID = 0x0B
_TYPE_MANUAL_ID = 0x0C

_ENTRY_HEADER = 6
_NULL = "(null)"


@dataclass(frozen=True)
class WamdMetadata:
    """Everything Fledermap uses from a `wamd` chunk. Absent fields are None."""

    model: str | None = None
    app_version: str | None = None
    device: str | None = None
    timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    auto_id: str | None = None
    manual_id: str | None = None


def _parse_timestamp(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_position(value: str) -> tuple[float | None, float | None, float | None]:
    """Parse `WGS84,<lat>,<lon>,<elevation>`; elevation may be the string `(null)`."""
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < 3:
        return None, None, None

    def _num(raw: str) -> float | None:
        if not raw or raw == _NULL:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    lat, lon = _num(parts[1]), _num(parts[2])
    elevation = _num(parts[3]) if len(parts) > 3 else None
    return lat, lon, elevation


def parse_wamd(payload: bytes) -> WamdMetadata:
    """Parse a `wamd` chunk payload. Never raises on malformed input."""
    fields: dict[str, object] = {}
    pos = 0
    while pos + _ENTRY_HEADER <= len(payload):
        type_id, size = struct.unpack_from("<HI", payload, pos)
        pos += _ENTRY_HEADER
        if pos + size > len(payload):
            break
        raw = payload[pos : pos + size]
        pos += size

        if type_id == _TYPE_MODEL:
            fields["model"] = raw.decode("utf-8", errors="replace")
        elif type_id == _TYPE_APP_VERSION:
            fields["app_version"] = raw.decode("utf-8", errors="replace")
        elif type_id == _TYPE_DEVICE:
            fields["device"] = raw.decode("utf-8", errors="replace")
        elif type_id == _TYPE_TIMESTAMP:
            fields["timestamp"] = _parse_timestamp(
                raw.decode("utf-8", errors="replace"),
            )
        elif type_id == _TYPE_POSITION:
            lat, lon, elev = _parse_position(raw.decode("utf-8", errors="replace"))
            fields["latitude"], fields["longitude"] = lat, lon
            fields["elevation_m"] = elev
        elif type_id == _TYPE_AUTO_ID:
            fields["auto_id"] = raw.decode("utf-8", errors="replace") or None
        elif type_id == _TYPE_MANUAL_ID:
            fields["manual_id"] = raw.decode("utf-8", errors="replace") or None

    return WamdMetadata(**fields)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_wamd.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/ingest/wamd.py tests/fixtures.py tests/test_wamd.py
git commit -m "feat: read Wildlife Acoustics wamd metadata chunk"
```

---

### Task 5: GUANO reader

The samples carry no `guan` chunk, but the EMT user guide states real recordings carry both. Spec D18: read both, prefer `guan`, fall back to `wamd`.

**Files:**
- Create: `src/fledermap/ingest/guano_read.py`
- Test: `tests/test_guano_read.py`

**Interfaces:**
- Consumes: `iter_chunks`, `read_chunk` from Task 2
- Produces:
  - `GuanoMetadata` — frozen dataclass with the same field names as `WamdMetadata`, plus `raw: dict[str, str]`
  - `parse_guano(path: Path) -> GuanoMetadata | None` — `None` when the file has no `guan` chunk

- [ ] **Step 1: Write the failing test**

`tests/test_guano_read.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fledermap.ingest.guano_read import parse_guano
from tests.fixtures import build_wav, fmt_payload

GUANO = (
    "GUANO|Version: 1.0\n"
    "Timestamp: 2026-08-21T21:45:32+02:00\n"
    "Make: Wildlife Acoustics, Inc.\n"
    "Model: Echo Meter Touch 2\n"
    "Samplerate: 256000\n"
    "Loc Position: 52.519400 13.401200\n"
    "Loc Elevation: 34.5\n"
    "Loc Accuracy: 6.0\n"
    "Species Auto ID: PIPPIP\n"
    "Species Manual ID: PIPPYG\n"
    "Note: field note\n"
)


def _wav(tmp_path: Path, guano: str | None) -> Path:
    chunks = [(b"fmt ", fmt_payload()), (b"data", b"\x00" * 8)]
    if guano is not None:
        chunks.append((b"guan", guano.encode("utf-8")))
    path = tmp_path / "a.wav"
    path.write_bytes(build_wav(chunks))
    return path


def test_returns_none_when_no_guan_chunk(tmp_path: Path) -> None:
    """The real EMT samples are exactly this case."""
    assert parse_guano(_wav(tmp_path, None)) is None


def test_parses_standard_fields(tmp_path: Path) -> None:
    meta = parse_guano(_wav(tmp_path, GUANO))

    assert meta is not None
    assert meta.model == "Echo Meter Touch 2"
    assert meta.timestamp == datetime(
        2026, 8, 21, 21, 45, 32, tzinfo=timezone(timedelta(hours=2)),
    )
    assert meta.latitude == 52.519400
    assert meta.longitude == 13.401200
    assert meta.elevation_m == 34.5
    assert meta.loc_accuracy_m == 6.0
    assert meta.auto_id == "PIPPIP"
    assert meta.manual_id == "PIPPYG"


def test_raw_keeps_every_key(tmp_path: Path) -> None:
    """Unmodelled keys must survive into `guano_raw` (spec section 5)."""
    meta = parse_guano(_wav(tmp_path, GUANO))

    assert meta is not None
    assert meta.raw["Note"] == "field note"
    assert "GUANO|Version" in meta.raw


def test_missing_position_is_none(tmp_path: Path) -> None:
    guano = "GUANO|Version: 1.0\nTimestamp: 2026-08-21T21:45:32+02:00\n"
    meta = parse_guano(_wav(tmp_path, guano))

    assert meta is not None
    assert meta.latitude is None
    assert meta.longitude is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_guano_read.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.ingest.guano_read'`.

- [ ] **Step 3: Implement**

`src/fledermap/ingest/guano_read.py`:

```python
"""Reader for the standard GUANO metadata chunk.

GUANO is UTF-8 text in a `guan` sub-chunk: newline-separated `Key: Value`
pairs, the first being `GUANO|Version`. Parsed directly rather than through
guano-py's file API so that reading costs one chunk read rather than a second
full open, and so a malformed chunk degrades instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fledermap.ingest.riff import NotARiffFileError, iter_chunks, read_chunk


@dataclass(frozen=True)
class GuanoMetadata:
    """Modelled GUANO fields, plus every key verbatim in `raw`."""

    model: str | None = None
    make: str | None = None
    serial: str | None = None
    app_version: str | None = None
    timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    loc_accuracy_m: float | None = None
    samplerate_hz: int | None = None
    te_factor: int | None = None
    note: str | None = None
    auto_id: str | None = None
    manual_id: str | None = None
    raw: dict[str, str] = field(default_factory=dict)


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value.strip()))
    except ValueError:
        return None


def _parse_position(value: str | None) -> tuple[float | None, float | None]:
    """`Loc Position` is two whitespace-separated floats: latitude longitude."""
    if value is None:
        return None, None
    parts = value.split()
    if len(parts) < 2:
        return None, None
    return _as_float(parts[0]), _as_float(parts[1])


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def parse_guano(path: Path) -> GuanoMetadata | None:
    """Return parsed GUANO metadata, or None when the file has no `guan` chunk."""
    try:
        chunk = next((c for c in iter_chunks(path) if c.chunk_id == "guan"), None)
    except NotARiffFileError:
        return None
    if chunk is None:
        return None

    text = read_chunk(path, chunk).decode("utf-8", errors="replace")
    raw: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        raw[key.strip()] = value.strip().replace("\\n", "\n")

    lat, lon = _parse_position(raw.get("Loc Position"))
    return GuanoMetadata(
        model=raw.get("Model"),
        make=raw.get("Make"),
        serial=raw.get("Serial"),
        app_version=raw.get("Firmware Version"),
        timestamp=_parse_timestamp(raw.get("Timestamp")),
        latitude=lat,
        longitude=lon,
        elevation_m=_as_float(raw.get("Loc Elevation")),
        loc_accuracy_m=_as_float(raw.get("Loc Accuracy")),
        samplerate_hz=_as_int(raw.get("Samplerate")),
        te_factor=_as_int(raw.get("TE")),
        note=raw.get("Note"),
        auto_id=raw.get("Species Auto ID") or None,
        manual_id=raw.get("Species Manual ID") or None,
        raw=raw,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_guano_read.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/ingest/guano_read.py tests/test_guano_read.py
git commit -m "feat: read standard GUANO metadata chunk"
```

---

### Task 6: EMT filename parser

`ID_YYYYMMDD_HHMMSS.WAV`, where `ID` is a six-letter genus+species code, `NoID`, or `NOISE`. Confirmed against the real samples: `EPTSER_20150610_215446.wav`.

**Files:**
- Create: `src/fledermap/domain/__init__.py`, `src/fledermap/domain/codes.py`
- Create: `src/fledermap/ingest/filename.py`
- Test: `tests/test_filename.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Verdict` — `enum.StrEnum` with members `SPECIES = "species"`, `NO_ID = "no_id"`, `NOISE = "noise"`
  - `IdSource` — `enum.StrEnum` with `EMT_GUANO = "emt.guano"`, `EMT_WAMD = "emt.wamd"`, `EMT_FILENAME = "emt.filename"`, `BATDETECT2 = "batdetect2"`, `BATTYBIRDNET = "battybirdnet"`, `KALEIDOSCOPE = "kaleidoscope"`, `MANUAL = "manual"` — seven members, matching the spec's identification-source vocabulary
  - `FilenameParse` — frozen dataclass: `code: str | None`, `verdict: Verdict`, `timestamp: datetime` (naive — the filename carries no offset)
  - `parse_emt_filename(name: str) -> FilenameParse | None`

- [ ] **Step 1: Write the failing test**

`tests/test_filename.py`:

```python
from __future__ import annotations

from datetime import datetime

import pytest

from fledermap.domain.codes import Verdict
from fledermap.ingest.filename import parse_emt_filename


def test_parses_species_filename() -> None:
    """Taken verbatim from the real sample files."""
    parsed = parse_emt_filename("EPTSER_20150610_215446.wav")

    assert parsed is not None
    assert parsed.code == "EPTSER"
    assert parsed.verdict is Verdict.SPECIES
    assert parsed.timestamp == datetime(2015, 6, 10, 21, 54, 46)


def test_parses_noid() -> None:
    parsed = parse_emt_filename("NoID_20260821_214532.wav")

    assert parsed is not None
    assert parsed.verdict is Verdict.NO_ID
    assert parsed.code is None


def test_parses_noise() -> None:
    parsed = parse_emt_filename("NOISE_20260821_220117.WAV")

    assert parsed is not None
    assert parsed.verdict is Verdict.NOISE
    assert parsed.code is None


def test_uppercase_extension_is_accepted() -> None:
    assert parse_emt_filename("MYODAU_20150623_213547.WAV") is not None


@pytest.mark.parametrize(
    "name",
    [
        "random.wav",
        "EPTSER_20150610.wav",
        "EPTSER_notadate_215446.wav",
        "EPTSER_20150632_215446.wav",
        "EPTSER_20150610_996146.wav",
        "",
    ],
)
def test_unparseable_names_return_none(name: str) -> None:
    assert parse_emt_filename(name) is None


def test_timestamp_is_naive() -> None:
    """The filename carries no timezone; merging decides what to do about that."""
    parsed = parse_emt_filename("EPTSER_20150610_215446.wav")

    assert parsed is not None
    assert parsed.timestamp.tzinfo is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_filename.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.domain'`.

- [ ] **Step 3: Implement the enums**

`src/fledermap/domain/__init__.py` — empty file.

`src/fledermap/domain/codes.py`:

```python
"""Vocabulary shared across ingest, storage, and the eventual web layer."""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    """What an identification actually asserts.

    `NoID` and `NOISE` are legitimate answers, not missing data, so they get a
    first-class representation rather than sentinel taxa (spec section 5).
    """

    SPECIES = "species"
    NO_ID = "no_id"
    NOISE = "noise"


class IdSource(StrEnum):
    """Where an identification came from. Sources coexist; they never overwrite."""

    EMT_GUANO = "emt.guano"
    EMT_WAMD = "emt.wamd"
    EMT_FILENAME = "emt.filename"
    BATDETECT2 = "batdetect2"
    BATTYBIRDNET = "battybirdnet"
    KALEIDOSCOPE = "kaleidoscope"
    MANUAL = "manual"
```

- [ ] **Step 4: Implement the parser**

`src/fledermap/ingest/filename.py`:

```python
"""Parser for the Echo Meter Touch filename convention.

    ID_YYYYMMDD_HHMMSS.WAV

`ID` is a six-letter genus+species code, or the literals `NoID` and `NOISE`.
The filename is a genuinely independent source of both timestamp and
identification, which is what makes it a useful cross-check on the embedded
metadata (spec section 11).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath

from fledermap.domain.codes import Verdict

_NO_ID = "noid"
_NOISE = "noise"


@dataclass(frozen=True)
class FilenameParse:
    """What the filename alone tells us. `timestamp` is naive: no offset is encoded."""

    code: str | None
    verdict: Verdict
    timestamp: datetime


def parse_emt_filename(name: str) -> FilenameParse | None:
    """Parse an EMT filename, or return None if it does not match the convention."""
    stem = PurePath(name).stem
    parts = stem.rsplit("_", 2)
    if len(parts) != 3:
        return None

    ident, date_part, time_part = parts
    try:
        timestamp = datetime.strptime(f"{date_part}{time_part}", "%Y%m%d%H%M%S")
    except ValueError:
        return None

    lowered = ident.lower()
    if lowered == _NO_ID:
        return FilenameParse(code=None, verdict=Verdict.NO_ID, timestamp=timestamp)
    if lowered == _NOISE:
        return FilenameParse(code=None, verdict=Verdict.NOISE, timestamp=timestamp)
    if not ident:
        return None
    return FilenameParse(
        code=ident.upper(), verdict=Verdict.SPECIES, timestamp=timestamp,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_filename.py -v`
Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/domain/__init__.py src/fledermap/domain/codes.py \
        src/fledermap/ingest/filename.py tests/test_filename.py
git commit -m "feat: parse Echo Meter Touch filenames"
```

---

### Task 7: Metadata merge and timestamp precedence

Combines the three sources into one record. This is where spec D17 lives: both timestamps are kept, `recorded_at` is computed, and disagreement is flagged rather than silently resolved.

**Files:**
- Create: `src/fledermap/domain/metadata.py`
- Create: `src/fledermap/ingest/merge.py`
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: `WamdMetadata` (Task 4), `GuanoMetadata` (Task 5), `FilenameParse` (Task 6), `Verdict`/`IdSource` (Task 6)
- Produces:
  - `ParsedIdentification` — frozen dataclass: `source: IdSource`, `source_version: str | None`, `verdict: Verdict`, `raw_label: str | None`
  - `RecordingMetadata` — frozen dataclass: `recorded_at: datetime`, `filename_at: datetime | None`, `metadata_at: datetime | None`, `timestamp_disagreement_s: float | None`, `latitude`, `longitude`, `elevation_m`, `samplerate_hz`, `duration_s`, `te_factor`, `make`, `model`, `serial`, `note`, `guano_raw: dict[str, str]`, `identifications: tuple[ParsedIdentification, ...]`
  - `merge_metadata(*, guano, wamd, filename, timestamp_source: str = "filename") -> RecordingMetadata`
  - `NoTimestampError(Exception)`

- [ ] **Step 1: Write the failing test**

`tests/test_merge.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fledermap.domain.codes import IdSource, Verdict
from fledermap.ingest.filename import parse_emt_filename
from fledermap.ingest.merge import NoTimestampError, merge_metadata
from fledermap.ingest.wamd import parse_wamd
from tests.fixtures import wamd_payload

BERLIN = timezone(timedelta(hours=2))


def test_filename_wins_by_default() -> None:
    """The provisional default. Metadata says 09:54, filename says 21:54."""
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.recorded_at.hour == 21
    assert result.filename_at == datetime(2015, 6, 10, 21, 54, 46)
    assert result.metadata_at == datetime(2015, 6, 10, 9, 54, 54, tzinfo=BERLIN)


def test_metadata_source_can_be_selected() -> None:
    """Flipping the config must not require re-ingesting (spec D17)."""
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
        timestamp_source="metadata",
    )

    assert result.recorded_at.hour == 9


def test_disagreement_is_measured() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.timestamp_disagreement_s is not None
    assert result.timestamp_disagreement_s > 3600


def test_agreeing_timestamps_report_no_disagreement() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(timestamp="2015-06-10 21:54:46+0200")),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.timestamp_disagreement_s == 0


def test_missing_timestamp_entirely_raises() -> None:
    with pytest.raises(NoTimestampError):
        merge_metadata(
            guano=None,
            wamd=parse_wamd(wamd_payload(timestamp=None)),
            filename=None,
        )


def test_falls_back_when_preferred_source_absent() -> None:
    """No parseable filename: use metadata rather than failing."""
    result = merge_metadata(
        guano=None, wamd=parse_wamd(wamd_payload()), filename=None,
    )

    assert result.recorded_at.hour == 9


def test_produces_one_identification_per_source() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(auto_id="MYODAU", manual_id="MYODAU")),
        filename=parse_emt_filename("MYODAU_20150623_213547.wav"),
    )

    sources = {i.source for i in result.identifications}

    assert IdSource.EMT_WAMD in sources
    assert IdSource.MANUAL in sources
    assert IdSource.EMT_FILENAME in sources


def test_noise_filename_yields_noise_verdict() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(auto_id=None, timestamp=None)),
        filename=parse_emt_filename("NOISE_20260821_220117.wav"),
    )

    verdicts = {i.verdict for i in result.identifications}

    assert Verdict.NOISE in verdicts


def test_position_prefers_guano_over_wamd() -> None:
    from fledermap.ingest.guano_read import GuanoMetadata

    guano = GuanoMetadata(latitude=52.5, longitude=13.4)
    result = merge_metadata(
        guano=guano,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.latitude == 52.5
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.domain.metadata'`.

- [ ] **Step 3: Implement the dataclasses**

`src/fledermap/domain/metadata.py`:

```python
"""Domain records produced by ingest. No I/O, no ORM, no framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fledermap.domain.codes import IdSource, Verdict


@dataclass(frozen=True)
class ParsedIdentification:
    """One source's claim about a recording. Sources coexist; none overwrites another."""

    source: IdSource
    source_version: str | None
    verdict: Verdict
    raw_label: str | None


@dataclass(frozen=True)
class RecordingMetadata:
    """Everything ingest knows about one file, before it reaches the database."""

    recorded_at: datetime
    filename_at: datetime | None = None
    metadata_at: datetime | None = None
    timestamp_disagreement_s: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    loc_accuracy_m: float | None = None
    samplerate_hz: int | None = None
    duration_s: float | None = None
    te_factor: int | None = None
    make: str | None = None
    model: str | None = None
    serial: str | None = None
    device: str | None = None  # host phone, from wamd; NOT the detector
    note: str | None = None
    guano_raw: dict[str, str] = field(default_factory=dict)
    identifications: tuple[ParsedIdentification, ...] = ()


@dataclass(frozen=True)
class ScannedFile:
    """One file as found on disk, ready to be committed to the database."""

    audio_hash: str
    path: Path
    metadata: RecordingMetadata
```

- [ ] **Step 4: Implement the merge**

`src/fledermap/ingest/merge.py`:

```python
"""Combine filename, GUANO, and wamd into one RecordingMetadata.

Timestamp precedence is deliberately configurable and both candidates are
retained (spec D17). The only available evidence is synthetic and disagrees
with itself by twelve hours, so the default is provisional, not a finding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.metadata import ParsedIdentification, RecordingMetadata
from fledermap.ingest.filename import FilenameParse
from fledermap.ingest.guano_read import GuanoMetadata
from fledermap.ingest.wamd import WamdMetadata

TIMESTAMP_SOURCE_FILENAME = "filename"
TIMESTAMP_SOURCE_METADATA = "metadata"


class NoTimestampError(Exception):
    """Neither the filename nor the embedded metadata yields a timestamp."""


_T = TypeVar("_T")


def _first(*values: _T | None) -> _T | None:
    """First non-None value, preserving its type so no `type: ignore` is needed."""
    return next((v for v in values if v is not None), None)


def _disagreement_seconds(
    filename_at: datetime | None, metadata_at: datetime | None,
) -> float | None:
    """Compare the two candidate timestamps, tolerating one being naive."""
    if filename_at is None or metadata_at is None:
        return None
    a, b = filename_at, metadata_at
    if a.tzinfo is None and b.tzinfo is not None:
        a = a.replace(tzinfo=b.tzinfo)
    elif b.tzinfo is None and a.tzinfo is not None:
        b = b.replace(tzinfo=a.tzinfo)
    return abs((a - b).total_seconds())


def _identifications(
    guano: GuanoMetadata | None,
    wamd: WamdMetadata | None,
    filename: FilenameParse | None,
) -> tuple[ParsedIdentification, ...]:
    out: list[ParsedIdentification] = []

    if filename is not None:
        out.append(
            ParsedIdentification(
                source=IdSource.EMT_FILENAME,
                source_version=None,
                verdict=filename.verdict,
                raw_label=filename.code,
            ),
        )

    for meta, source, version in (
        (guano, IdSource.EMT_GUANO, getattr(guano, "app_version", None)),
        (wamd, IdSource.EMT_WAMD, getattr(wamd, "app_version", None)),
    ):
        if meta is None:
            continue
        if meta.auto_id:
            out.append(
                ParsedIdentification(
                    source=source,
                    source_version=version,
                    verdict=Verdict.SPECIES,
                    raw_label=meta.auto_id,
                ),
            )
        if meta.manual_id:
            out.append(
                ParsedIdentification(
                    source=IdSource.MANUAL,
                    source_version=None,
                    verdict=Verdict.SPECIES,
                    raw_label=meta.manual_id,
                ),
            )

    return tuple(out)


def merge_metadata(
    *,
    guano: GuanoMetadata | None,
    wamd: WamdMetadata | None,
    filename: FilenameParse | None,
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME,
) -> RecordingMetadata:
    """Merge every available source. Raises NoTimestampError if none yields a time."""
    filename_at = filename.timestamp if filename else None
    metadata_at = _first(
        getattr(guano, "timestamp", None), getattr(wamd, "timestamp", None),
    )

    preferred, fallback = (
        (filename_at, metadata_at)
        if timestamp_source == TIMESTAMP_SOURCE_FILENAME
        else (metadata_at, filename_at)
    )
    recorded_at = preferred if preferred is not None else fallback
    if recorded_at is None:
        msg = "no timestamp available from filename or embedded metadata"
        raise NoTimestampError(msg)
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)

    return RecordingMetadata(
        recorded_at=recorded_at,
        filename_at=filename_at,
        metadata_at=metadata_at,
        timestamp_disagreement_s=_disagreement_seconds(filename_at, metadata_at),
        latitude=_first(
            getattr(guano, "latitude", None), getattr(wamd, "latitude", None),
        ),
        longitude=_first(
            getattr(guano, "longitude", None), getattr(wamd, "longitude", None),
        ),
        elevation_m=_first(
            getattr(guano, "elevation_m", None), getattr(wamd, "elevation_m", None),
        ),
        loc_accuracy_m=getattr(guano, "loc_accuracy_m", None),
        samplerate_hz=getattr(guano, "samplerate_hz", None),
        te_factor=getattr(guano, "te_factor", None),
        make=getattr(guano, "make", None),
        model=_first(
            getattr(guano, "model", None), getattr(wamd, "model", None),
        ),
        serial=getattr(guano, "serial", None),
        device=getattr(wamd, "device", None),
        note=getattr(guano, "note", None),
        guano_raw=dict(getattr(guano, "raw", {}) or {}),
        identifications=_identifications(guano, wamd, filename),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_merge.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/domain/metadata.py src/fledermap/ingest/merge.py tests/test_merge.py
git commit -m "feat: merge filename, GUANO and wamd metadata"
```

---

### Task 8: Scan orchestration

Walks a tree and emits `ScannedFile`s. Includes the settle rule — Syncthing and rsync expose partially-written files, and ingesting a truncated WAV on the first night is the failure this prevents.

**Files:**
- Create: `src/fledermap/ingest/scan.py`
- Test: `tests/test_scan.py`

**Interfaces:**
- Consumes: `audio_hash` (3), `parse_wamd` (4), `parse_guano` (5), `parse_emt_filename` (6), `merge_metadata` (7), `ScannedFile` (7)
- Produces:
  - `scan(root: Path, *, timestamp_source: str = "filename", settle_seconds: float = 30.0, now: float | None = None) -> Iterator[ScannedFile]`
  - `SkipReason` — `StrEnum`: `NOT_A_WAV`, `UNSETTLED`, `UNPARSEABLE`
  - `scan_with_skips(...) -> Iterator[ScannedFile | tuple[Path, SkipReason]]`

- [ ] **Step 1: Write the failing test**

`tests/test_scan.py`:

```python
from __future__ import annotations

import os
import time
from pathlib import Path

from fledermap.ingest.scan import scan
from tests.fixtures import build_wav, fmt_payload, wamd_payload


def _emt_file(directory: Path, name: str, *, audio: bytes = b"\x01\x02" * 32) -> Path:
    path = directory / name
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload()),
                (b"data", audio),
                (b"wamd", wamd_payload()),
            ],
        ),
    )
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def test_scans_nested_directories(tmp_path: Path) -> None:
    session = tmp_path / "Session_20130401_053030"
    session.mkdir()
    _emt_file(session, "EPTSER_20150610_215446.wav")
    _emt_file(session, "MYODAU_20150623_213547.wav", audio=b"\x09\x08" * 32)

    results = list(scan(tmp_path))

    assert len(results) == 2
    assert {r.path.name for r in results} == {
        "EPTSER_20150610_215446.wav",
        "MYODAU_20150623_213547.wav",
    }


def test_non_wav_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not a recording")
    (tmp_path / "fake.wav").write_bytes(b"definitely not RIFF")
    _emt_file(tmp_path, "EPTSER_20150610_215446.wav")

    assert len(list(scan(tmp_path))) == 1


def test_recently_written_files_are_skipped(tmp_path: Path) -> None:
    """The settle rule: a file mid-sync must not be ingested truncated."""
    path = tmp_path / "EPTSER_20150610_215446.wav"
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload()), (b"data", b"\x01\x02"), (b"wamd", wamd_payload())]),
    )
    os.utime(path, None)  # mtime = now

    assert list(scan(tmp_path, settle_seconds=30.0)) == []


def test_settled_files_are_included(tmp_path: Path) -> None:
    _emt_file(tmp_path, "EPTSER_20150610_215446.wav")

    assert len(list(scan(tmp_path, settle_seconds=30.0))) == 1


def test_scan_populates_hash_and_metadata(tmp_path: Path) -> None:
    _emt_file(tmp_path, "EPTSER_20150610_215446.wav")

    result = next(iter(scan(tmp_path)))

    assert len(result.audio_hash) == 64
    assert result.metadata.recorded_at.hour == 21
    assert result.metadata.latitude == 42.346973
    assert result.metadata.samplerate_hz == 256000
    assert result.metadata.duration_s is not None


def test_scan_does_not_modify_the_archive(tmp_path: Path) -> None:
    """Ingest is strictly read-only on archive_root."""
    path = _emt_file(tmp_path, "EPTSER_20150610_215446.wav")
    before = (path.stat().st_mtime, path.read_bytes())

    list(scan(tmp_path))

    assert (path.stat().st_mtime, path.read_bytes()) == before
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.ingest.scan'`.

- [ ] **Step 3: Implement**

`src/fledermap/ingest/scan.py`:

```python
"""Walk an archive directory and emit one ScannedFile per readable recording.

Pure with respect to the archive: opens files read-only and never writes,
moves, renames, or deletes. See spec section 6.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

from fledermap.domain.metadata import ScannedFile
from fledermap.ingest.filename import parse_emt_filename
from fledermap.ingest.guano_read import parse_guano
from fledermap.ingest.merge import (
    TIMESTAMP_SOURCE_FILENAME,
    NoTimestampError,
    merge_metadata,
)
from fledermap.ingest.riff import (
    MissingAudioChunkError,
    NotARiffFileError,
    audio_hash,
    iter_chunks,
    read_chunk,
    read_format,
)
from fledermap.ingest.wamd import parse_wamd

logger = logging.getLogger(__name__)

DEFAULT_SETTLE_SECONDS = 30.0
_TEMP_SUFFIXES = (".tmp", ".part", ".syncthing", ".!sync")


class SkipReason(StrEnum):
    """Why a file present in the archive produced no ScannedFile."""

    NOT_A_WAV = "not_a_wav"
    UNSETTLED = "unsettled"
    UNPARSEABLE = "unparseable"


def _is_settled(path: Path, settle_seconds: float, now: float) -> bool:
    """A file still being written by Syncthing or rsync must not be read yet."""
    if any(part.startswith(".") for part in path.parts):
        return False
    if path.name.endswith(_TEMP_SUFFIXES):
        return False
    return (now - path.stat().st_mtime) >= settle_seconds


def _scan_one(path: Path, timestamp_source: str) -> ScannedFile | SkipReason:
    try:
        digest = audio_hash(path)
    except (NotARiffFileError, MissingAudioChunkError, OSError):
        return SkipReason.NOT_A_WAV

    wamd = None
    try:
        chunk = next((c for c in iter_chunks(path) if c.chunk_id == "wamd"), None)
        if chunk is not None:
            wamd = parse_wamd(read_chunk(path, chunk))
    except (NotARiffFileError, OSError):
        wamd = None

    try:
        metadata = merge_metadata(
            guano=parse_guano(path),
            wamd=wamd,
            filename=parse_emt_filename(path.name),
            timestamp_source=timestamp_source,
        )
    except NoTimestampError:
        logger.warning("no timestamp for %s; skipping", path)
        return SkipReason.UNPARSEABLE

    # Samplerate and duration come from the container itself, which is more
    # reliable than any metadata field and present even when metadata is not.
    fmt = read_format(path)
    metadata = replace(
        metadata,
        samplerate_hz=metadata.samplerate_hz or fmt.samplerate_hz,
        duration_s=fmt.duration_s,
    )

    return ScannedFile(audio_hash=digest, path=path, metadata=metadata)


def scan(
    root: Path,
    *,
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
) -> Iterator[ScannedFile]:
    """Yield a ScannedFile for every readable, settled recording under `root`."""
    for scanned in scan_with_skips(
        root,
        timestamp_source=timestamp_source,
        settle_seconds=settle_seconds,
        now=now,
    ):
        if isinstance(scanned, ScannedFile):
            yield scanned


def scan_with_skips(
    root: Path,
    *,
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
) -> Iterator[ScannedFile | tuple[Path, SkipReason]]:
    """As `scan`, but also reports why files were skipped, for CLI summaries."""
    clock = time.time() if now is None else now
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if not _is_settled(path, settle_seconds, clock):
            yield (path, SkipReason.UNSETTLED)
            continue
        result = _scan_one(path, timestamp_source)
        yield result if isinstance(result, ScannedFile) else (path, result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_scan.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/ingest/scan.py tests/test_scan.py
git commit -m "feat: scan archive directories into ScannedFile records"
```

---

### Task 9: Database models and first migration

**Files:**
- Create: `src/fledermap/store/__init__.py`, `src/fledermap/store/models.py`, `src/fledermap/store/db.py`
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`
- Create: `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `Verdict`, `IdSource` (Task 6)
- Produces:
  - `Base` — SQLAlchemy `DeclarativeBase`
  - `Recording`, `Identification`, `Taxon`, `TaxonCode`, `Session` ORM classes
  - `make_engine(url: str) -> Engine`, `session_factory(engine) -> sessionmaker[Session]`
  - `tests.conftest.postgis_url` fixture (session-scoped, testcontainers)

- [ ] **Step 1: Write the container fixture**

`tests/conftest.py`:

```python
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from testcontainers.postgres import PostgresContainer

from fledermap.store.db import make_engine
from fledermap.store.models import Base


@pytest.fixture(scope="session")
def postgis_url() -> Iterator[str]:
    """A throwaway PostGIS instance. Mirrors poiidx's testing approach."""
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        yield container.get_connection_url()


@pytest.fixture
def engine(postgis_url: str) -> Iterator[Engine]:
    eng = make_engine(postgis_url)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()
```

- [ ] **Step 2: Write the failing test**

`tests/test_models.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording

pytestmark = pytest.mark.db


def _recording(digest: str = "a" * 64, path: str = "s/EPTSER_20150610_215446.wav") -> Recording:
    return Recording(
        audio_hash=digest,
        path=path,
        recorded_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=timezone.utc),
        samplerate_hz=256000,
    )


def test_recording_round_trips(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(_recording())
        session.commit()

    with OrmSession(engine) as session:
        found = session.scalars(select(Recording)).one()
        assert found.samplerate_hz == 256000
        assert found.missing_since is None


def test_audio_hash_is_unique(engine: Engine) -> None:
    """Identity is the audio; the same payload twice is the same recording."""
    with OrmSession(engine) as session:
        session.add(_recording(path="a.wav"))
        session.add(_recording(path="b.wav"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_geometry_may_be_null(engine: Engine) -> None:
    """Recordings without GPS are first-class, not errors."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.geom = None
        session.add(rec)
        session.commit()
        assert session.scalars(select(Recording)).one().geom is None


def test_identifications_cascade_from_recording(engine: Engine) -> None:
    with OrmSession(engine) as session:
        rec = _recording()
        rec.identifications = [
            Identification(
                source=IdSource.EMT_WAMD,
                source_version="App 3.1.10",
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
            Identification(
                source=IdSource.EMT_FILENAME,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        ]
        session.add(rec)
        session.commit()

    with OrmSession(engine) as session:
        assert len(session.scalars(select(Recording)).one().identifications) == 2


def test_both_timestamp_columns_persist(engine: Engine) -> None:
    """Spec D17: neither candidate may be dropped as 'unused'."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.filename_at = datetime(2015, 6, 10, 21, 54, 46, tzinfo=timezone.utc)
        rec.metadata_at = datetime(2015, 6, 10, 9, 54, 54, tzinfo=timezone.utc)
        rec.timestamp_disagreement_s = 43192.0
        session.add(rec)
        session.commit()

    with OrmSession(engine) as session:
        found = session.scalars(select(Recording)).one()
        assert found.filename_at != found.metadata_at
        assert found.timestamp_disagreement_s == pytest.approx(43192.0)
```

- [ ] **Step 3: Run it to verify it fails**

Run: `hatch test tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.store'`. Requires a running Docker daemon.

- [ ] **Step 4: Implement the models**

`src/fledermap/store/__init__.py` — empty file.

`src/fledermap/store/db.py`:

```python
"""Engine and session construction.

WARNING: the URL here must never point at poiidx's database. poiidx hashes its
own schema and filter config on init and DROPS AND RECREATES ALL TABLES on any
mismatch, which would destroy Fledermap's data. Fledermap uses `bats_db`;
poiidx uses `poiidx_bats_db`. They are separate databases by design (spec D11).
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from fledermap.store.models import Base


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Create an engine for Fledermap's own database."""
    return create_engine(url, echo=echo, future=True)


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Test and development convenience only; production schema comes from Alembic."""
    Base.metadata.create_all(engine)
```

`src/fledermap/store/models.py`:

```python
"""SQLAlchemy models. See spec section 5."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from fledermap.domain.codes import IdSource, Verdict


class Base(DeclarativeBase):
    pass


class Recording(Base):
    """One audio file. Identity is `audio_hash`; `path` is mutable (spec D8)."""

    __tablename__ = "recording"

    id: Mapped[int] = mapped_column(primary_key=True)
    audio_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(Text, index=True)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    filename_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timestamp_disagreement_s: Mapped[float | None] = mapped_column(Float)

    geom: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True,
    )
    loc_accuracy_m: Mapped[float | None] = mapped_column(Float)
    elevation_m: Mapped[float | None] = mapped_column(Float)

    samplerate_hz: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    te_factor: Mapped[int | None] = mapped_column(Integer)

    make: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    serial: Mapped[str | None] = mapped_column(String(128))
    device: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)

    guano_raw: Mapped[dict] = mapped_column(JSONB, default=dict)

    session_id: Mapped[int | None] = mapped_column(ForeignKey("session.id"))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identifications: Mapped[list[Identification]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Identification(Base):
    """One source's claim. Sources coexist; `superseded_at` records changes of mind."""

    __tablename__ = "identification"
    __table_args__ = (
        UniqueConstraint(
            "recording_id",
            "source",
            "source_version",
            "raw_label",
            name="uq_identification_source_claim",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"), index=True,
    )
    source: Mapped[IdSource] = mapped_column(String(32))
    source_version: Mapped[str | None] = mapped_column(String(64))
    verdict: Mapped[Verdict] = mapped_column(String(16))
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    raw_label: Mapped[str | None] = mapped_column(String(128))
    confidence: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recording: Mapped[Recording] = relationship(back_populates="identifications")


class Taxon(Base):
    """A species, genus, or phonic group. Not every identification is a species."""

    __tablename__ = "taxon"

    id: Mapped[int] = mapped_column(primary_key=True)
    rank: Mapped[str] = mapped_column(String(16))
    scientific_name: Mapped[str] = mapped_column(String(128), unique=True)
    common_name_de: Mapped[str | None] = mapped_column(String(128))
    common_name_en: Mapped[str | None] = mapped_column(String(128))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))


class TaxonCode(Base):
    """Per-source vocabulary. WA codes are not a universal key (spec D10)."""

    __tablename__ = "taxon_code"
    __table_args__ = (UniqueConstraint("source", "code", name="uq_taxon_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(32), index=True)
    taxon_id: Mapped[int] = mapped_column(ForeignKey("taxon.id"))


class Session(Base):
    """The durable annotation layer. Incremental, never renumbered (spec D7)."""

    __tablename__ = "session"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16), default="stationary")
    detector_key: Mapped[str | None] = mapped_column(String(160), index=True)
    note: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 5: Add Alembic**

Run: `hatch run alembic init alembic`

Then set in `alembic.ini`: `sqlalchemy.url =` (left blank — supplied at runtime), and in `alembic/env.py` replace the `target_metadata` line with:

```python
from fledermap.store.models import Base

target_metadata = Base.metadata
```

Generate the first migration:

```bash
hatch run alembic revision --autogenerate -m "initial schema"
```

Rename the generated file to `alembic/versions/0001_initial.py` and confirm it creates all five tables plus the PostGIS geography column.

- [ ] **Step 6: Run tests to verify they pass**

Run: `hatch test tests/test_models.py -v`
Expected: 5 passed. Requires Docker.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/store/ alembic.ini alembic/ tests/conftest.py tests/test_models.py
git commit -m "feat: add database models and initial migration"
```

---

### Task 10: Taxonomy seed

**Files:**
- Create: `src/fledermap/store/seed.py`, `src/fledermap/store/data/taxa_eu.yaml`
- Test: `tests/test_seed.py`

**Interfaces:**
- Consumes: `Taxon`, `TaxonCode` (Task 9)
- Produces: `seed_taxonomy(session: OrmSession) -> int` (returns taxa created); `resolve_code(session, source: str, code: str) -> Taxon | None`

- [ ] **Step 1: Create the seed data**

`src/fledermap/store/data/taxa_eu.yaml` — the European species the EMT reports, with Wildlife Acoustics codes. Extend as needed; unmapped labels are handled gracefully by design.

```yaml
taxa:
  - scientific_name: Pipistrellus pipistrellus
    rank: species
    common_name_de: Zwergfledermaus
    common_name_en: Common pipistrelle
    codes: {emt: PIPPIP}
  - scientific_name: Pipistrellus pygmaeus
    rank: species
    common_name_de: Mückenfledermaus
    common_name_en: Soprano pipistrelle
    codes: {emt: PIPPYG}
  - scientific_name: Pipistrellus nathusii
    rank: species
    common_name_de: Rauhautfledermaus
    common_name_en: Nathusius' pipistrelle
    codes: {emt: PIPNAT}
  - scientific_name: Nyctalus noctula
    rank: species
    common_name_de: Abendsegler
    common_name_en: Common noctule
    codes: {emt: NYCNOC}
  - scientific_name: Nyctalus leisleri
    rank: species
    common_name_de: Kleinabendsegler
    common_name_en: Leisler's bat
    codes: {emt: NYCLEI}
  - scientific_name: Eptesicus serotinus
    rank: species
    common_name_de: Breitflügelfledermaus
    common_name_en: Serotine
    codes: {emt: EPTSER}
  - scientific_name: Myotis daubentonii
    rank: species
    common_name_de: Wasserfledermaus
    common_name_en: Daubenton's bat
    codes: {emt: MYODAU}
  - scientific_name: Myotis myotis
    rank: species
    common_name_de: Großes Mausohr
    common_name_en: Greater mouse-eared bat
    codes: {emt: MYOMYO}
  - scientific_name: Barbastella barbastellus
    rank: species
    common_name_de: Mopsfledermaus
    common_name_en: Western barbastelle
    codes: {emt: BARBAR}
  - scientific_name: Plecotus auritus
    rank: species
    common_name_de: Braunes Langohr
    common_name_en: Brown long-eared bat
    codes: {emt: PLEAUR}
  - scientific_name: Myotis
    rank: genus
    common_name_de: Mausohren
    common_name_en: Mouse-eared bats
    codes: {emt: MYOSPP}
  - scientific_name: Nyctaloid
    rank: group
    common_name_de: Nyctaloid
    common_name_en: Nyctaloid
    codes: {}
```

- [ ] **Step 2: Write the failing test**

`tests/test_seed.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Taxon, TaxonCode
from fledermap.store.seed import resolve_code, seed_taxonomy

pytestmark = pytest.mark.db


def test_seeding_creates_taxa_and_codes(engine: Engine) -> None:
    with OrmSession(engine) as session:
        created = seed_taxonomy(session)
        session.commit()

        assert created > 0
        assert session.scalar(select(func.count()).select_from(TaxonCode)) > 0


def test_seeding_is_idempotent(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()
        first = session.scalar(select(func.count()).select_from(Taxon))

        seed_taxonomy(session)
        session.commit()

        assert session.scalar(select(func.count()).select_from(Taxon)) == first


def test_resolves_a_known_code(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = resolve_code(session, "emt", "EPTSER")

        assert taxon is not None
        assert taxon.scientific_name == "Eptesicus serotinus"
        assert taxon.common_name_de == "Breitflügelfledermaus"


def test_group_and_genus_ranks_are_representable(engine: Engine) -> None:
    """MYOSPP is a genus, Nyctaloid is a phonic group. Neither is a species."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        assert resolve_code(session, "emt", "MYOSPP").rank == "genus"
        assert session.scalars(
            select(Taxon).where(Taxon.scientific_name == "Nyctaloid"),
        ).one().rank == "group"


def test_unknown_code_resolves_to_none(engine: Engine) -> None:
    """Unmapped labels must not raise — they become a review queue (spec section 5)."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        assert resolve_code(session, "emt", "ZZZZZZ") is None
```

- [ ] **Step 3: Run it to verify it fails**

Run: `hatch test tests/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.store.seed'`.

- [ ] **Step 4: Implement**

`src/fledermap/store/seed.py`:

```python
"""Seed the taxonomy from bundled YAML. Idempotent: safe to run on every startup."""

from __future__ import annotations

from importlib.resources import files

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Taxon, TaxonCode

_DATA = "taxa_eu.yaml"


def _load() -> list[dict]:
    raw = files("fledermap.store.data").joinpath(_DATA).read_text(encoding="utf-8")
    return yaml.safe_load(raw)["taxa"]


def seed_taxonomy(session: OrmSession) -> int:
    """Insert any missing taxa and codes. Returns the number of taxa created."""
    created = 0
    for entry in _load():
        taxon = session.scalars(
            select(Taxon).where(Taxon.scientific_name == entry["scientific_name"]),
        ).one_or_none()
        if taxon is None:
            taxon = Taxon(
                rank=entry["rank"],
                scientific_name=entry["scientific_name"],
                common_name_de=entry.get("common_name_de"),
                common_name_en=entry.get("common_name_en"),
            )
            session.add(taxon)
            session.flush()
            created += 1

        for source, code in (entry.get("codes") or {}).items():
            exists = session.scalars(
                select(TaxonCode).where(
                    TaxonCode.source == source, TaxonCode.code == code,
                ),
            ).one_or_none()
            if exists is None:
                session.add(TaxonCode(source=source, code=code, taxon_id=taxon.id))

    return created


def resolve_code(session: OrmSession, source: str, code: str) -> Taxon | None:
    """Map a source-specific label to a taxon, or None when unmapped."""
    return session.scalars(
        select(Taxon)
        .join(TaxonCode, TaxonCode.taxon_id == Taxon.id)
        .where(TaxonCode.source == source, TaxonCode.code == code),
    ).one_or_none()
```

Add to `pyproject.toml` under `[tool.hatch.build.targets.wheel]`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/fledermap"]

[tool.hatch.build.targets.wheel.force-include]
"src/fledermap/store/data" = "fledermap/store/data"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_seed.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/store/seed.py src/fledermap/store/data/taxa_eu.yaml \
        pyproject.toml tests/test_seed.py
git commit -m "feat: seed European bat taxonomy and Wildlife Acoustics codes"
```

---

### Task 11: `commit_scan` — the resolution table

The heart of the phase. Implements the four cases in spec section 6, and the idempotency property.

**Files:**
- Create: `src/fledermap/services/__init__.py`, `src/fledermap/services/ingest.py`
- Test: `tests/test_ingest_service.py`

**Interfaces:**
- Consumes: `ScannedFile` (7), `Recording`/`Identification` (9), `resolve_code` (10)
- Produces:
  - `IngestOutcome` — `StrEnum`: `CREATED`, `UNCHANGED`, `UPDATED`, `MOVED`, `REPLACED`
  - `IngestReport` — dataclass with counters `created`, `unchanged`, `updated`, `moved`, `replaced`, and `unmapped_labels: set[str]`
  - `commit_scan(session, scanned: Iterable[ScannedFile], *, archive_root: Path) -> IngestReport`

- [ ] **Step 1: Write the failing test**

`tests/test_ingest_service.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.metadata import (
    ParsedIdentification,
    RecordingMetadata,
    ScannedFile,
)
from fledermap.services.ingest import commit_scan
from fledermap.store.models import Identification, Recording
from fledermap.store.seed import seed_taxonomy

pytestmark = pytest.mark.db

ROOT = Path("/archive")


def _scanned(
    digest: str = "a" * 64,
    name: str = "EPTSER_20150610_215446.wav",
    label: str = "EPTSER",
) -> ScannedFile:
    return ScannedFile(
        audio_hash=digest,
        path=ROOT / "Session_20130401_053030" / name,
        metadata=RecordingMetadata(
            recorded_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=timezone.utc),
            samplerate_hz=256000,
            latitude=42.346973,
            longitude=-76.48760,
            identifications=(
                ParsedIdentification(
                    source=IdSource.EMT_WAMD,
                    source_version="App 3.1.10",
                    verdict=Verdict.SPECIES,
                    raw_label=label,
                ),
            ),
        ),
    )


def test_new_file_is_created(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        report = commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        assert report.created == 1
        assert session.scalar(select(func.count()).select_from(Recording)) == 1


def test_ingest_is_idempotent(engine: Engine) -> None:
    """Run twice, nothing changes. The defining property of spec section 6."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        report = commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        assert report.created == 0
        assert report.unchanged == 1
        assert session.scalar(select(func.count()).select_from(Recording)) == 1


def test_rename_updates_path_without_duplicating(engine: Engine) -> None:
    """The re-ID case: same audio, new filename. This is why identity is the hash."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(name="NoID_20150610_215446.wav")], archive_root=ROOT)
        session.commit()

        report = commit_scan(
            session, [_scanned(name="EPTSER_20150610_215446.wav")], archive_root=ROOT,
        )
        session.commit()

        assert report.moved == 1
        assert session.scalar(select(func.count()).select_from(Recording)) == 1
        assert session.scalars(select(Recording)).one().path.endswith(
            "EPTSER_20150610_215446.wav",
        )


def test_changed_identification_supersedes_the_old_one(engine: Engine) -> None:
    """The EMT changing its mind is recorded, not overwritten."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(label="MYODAU")], archive_root=ROOT)
        session.commit()

        commit_scan(session, [_scanned(label="EPTSER")], archive_root=ROOT)
        session.commit()

        ids = session.scalars(select(Identification)).all()
        assert len(ids) == 2
        superseded = [i for i in ids if i.superseded_at is not None]
        assert len(superseded) == 1
        assert superseded[0].raw_label == "MYODAU"


def test_paths_are_stored_relative_to_archive_root(engine: Engine) -> None:
    """So the archive can move without rewriting every row."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        path = session.scalars(select(Recording)).one().path
        assert not path.startswith("/")
        assert path.startswith("Session_20130401_053030/")


def test_known_label_resolves_to_taxon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(label="EPTSER")], archive_root=ROOT)
        session.commit()

        ident = session.scalars(select(Identification)).one()
        assert ident.taxon_id is not None


def test_unmapped_label_is_stored_and_reported(engine: Engine) -> None:
    """Ingest must not fail on an unknown code; it becomes a review item."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        report = commit_scan(session, [_scanned(label="ZZZZZZ")], archive_root=ROOT)
        session.commit()

        ident = session.scalars(select(Identification)).one()
        assert ident.taxon_id is None
        assert ident.raw_label == "ZZZZZZ"
        assert "ZZZZZZ" in report.unmapped_labels


def test_geometry_is_written_when_position_present(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        assert session.scalars(select(Recording)).one().geom is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_ingest_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.services'`.

- [ ] **Step 3: Implement**

`src/fledermap/services/__init__.py` — empty file.

`src/fledermap/services/ingest.py`:

```python
"""Resolve scanned files against the database. See spec section 6.

Idempotent by construction: identity is `audio_hash`, so re-running ingest over
an unchanged archive produces no writes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.metadata import ParsedIdentification, ScannedFile
from fledermap.store.models import Identification, Recording
from fledermap.store.seed import resolve_code

_EMT_SOURCES = {"emt.guano", "emt.wamd", "emt.filename"}


class IngestOutcome(StrEnum):
    CREATED = "created"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    MOVED = "moved"
    REPLACED = "replaced"


@dataclass
class IngestReport:
    created: int = 0
    unchanged: int = 0
    updated: int = 0
    moved: int = 0
    replaced: int = 0
    unmapped_labels: set[str] = field(default_factory=set)

    def record(self, outcome: IngestOutcome) -> None:
        setattr(self, outcome.value, getattr(self, outcome.value) + 1)

    @property
    def total(self) -> int:
        return self.created + self.unchanged + self.updated + self.moved + self.replaced


def _relative(path: Path, archive_root: Path) -> str:
    try:
        return str(path.relative_to(archive_root))
    except ValueError:
        return str(path)


def _code_source(source: str) -> str:
    """All Echo Meter Touch sources share the Wildlife Acoustics vocabulary."""
    return "emt" if source in _EMT_SOURCES else source


def _apply_identifications(
    session: OrmSession,
    recording: Recording,
    parsed: tuple[ParsedIdentification, ...],
    report: IngestReport,
    now: datetime,
) -> bool:
    """Add new claims and supersede ones this source no longer makes."""
    changed = False
    incoming = {(p.source, p.source_version, p.raw_label) for p in parsed}
    existing = {
        (i.source, i.source_version, i.raw_label): i
        for i in recording.identifications
        if i.superseded_at is None
    }

    for key, ident in existing.items():
        if key not in incoming and ident.source in _EMT_SOURCES:
            ident.superseded_at = now
            changed = True

    for p in parsed:
        key = (p.source, p.source_version, p.raw_label)
        if key in existing:
            continue
        taxon = None
        if p.raw_label:
            taxon = resolve_code(session, _code_source(p.source), p.raw_label)
            if taxon is None:
                report.unmapped_labels.add(p.raw_label)
        recording.identifications.append(
            Identification(
                source=p.source,
                source_version=p.source_version,
                verdict=p.verdict,
                taxon_id=taxon.id if taxon else None,
                raw_label=p.raw_label,
                first_seen_at=now,
            ),
        )
        changed = True

    return changed


def _apply_metadata(recording: Recording, scanned: ScannedFile) -> None:
    m = scanned.metadata
    recording.recorded_at = m.recorded_at
    recording.filename_at = m.filename_at
    recording.metadata_at = m.metadata_at
    recording.timestamp_disagreement_s = m.timestamp_disagreement_s
    recording.elevation_m = m.elevation_m
    recording.loc_accuracy_m = m.loc_accuracy_m
    recording.samplerate_hz = m.samplerate_hz
    recording.duration_s = m.duration_s
    recording.te_factor = m.te_factor
    recording.make = m.make
    recording.model = m.model
    recording.serial = m.serial
    recording.note = m.note
    recording.guano_raw = dict(m.guano_raw)
    if m.latitude is not None and m.longitude is not None:
        recording.geom = from_shape(Point(m.longitude, m.latitude), srid=4326)


def commit_scan(
    session: OrmSession,
    scanned: Iterable[ScannedFile],
    *,
    archive_root: Path,
) -> IngestReport:
    """Write scanned files to the database, resolving each by `audio_hash`."""
    report = IngestReport()
    now = datetime.now(tz=timezone.utc)

    for item in scanned:
        rel = _relative(item.path, archive_root)
        existing = session.scalars(
            select(Recording).where(Recording.audio_hash == item.audio_hash),
        ).one_or_none()

        if existing is None:
            recording = Recording(
                audio_hash=item.audio_hash, path=rel, ingested_at=now,
            )
            _apply_metadata(recording, item)
            session.add(recording)
            session.flush()
            _apply_identifications(
                session, recording, item.metadata.identifications, report, now,
            )
            report.record(IngestOutcome.CREATED)
            continue

        moved = existing.path != rel
        existing.path = rel
        existing.missing_since = None
        before = (
            existing.guano_raw,
            existing.recorded_at,
            existing.metadata_at,
        )
        _apply_metadata(existing, item)
        metadata_changed = before != (
            existing.guano_raw,
            existing.recorded_at,
            existing.metadata_at,
        )
        ids_changed = _apply_identifications(
            session, existing, item.metadata.identifications, report, now,
        )

        if moved:
            report.record(IngestOutcome.MOVED)
        elif metadata_changed or ids_changed:
            report.record(IngestOutcome.UPDATED)
        else:
            report.record(IngestOutcome.UNCHANGED)

    return report
```

Add `shapely` to `dependencies` in `pyproject.toml`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_ingest_service.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/ pyproject.toml tests/test_ingest_service.py
git commit -m "feat: resolve scanned files against the database by audio hash"
```

---

### Task 12: Missing-file sweep with the mass-disappearance guard

An unmounted drive or a mid-sync Syncthing makes every file look deleted at once. Without the guard, one unmounted NAS silently flags the entire dataset (spec section 6).

**Files:**
- Modify: `src/fledermap/services/ingest.py`
- Test: `tests/test_missing_sweep.py`

**Interfaces:**
- Consumes: `Recording` (9)
- Produces:
  - `MassDisappearanceError(Exception)` — carries `missing: int`, `known: int`
  - `sweep_missing(session, seen_hashes: set[str], *, threshold: float = 0.10) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_missing_sweep.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.ingest import MassDisappearanceError, sweep_missing
from fledermap.store.models import Recording

pytestmark = pytest.mark.db


def _add(session: OrmSession, count: int) -> list[str]:
    hashes = [f"{i:064d}" for i in range(count)]
    for i, digest in enumerate(hashes):
        session.add(
            Recording(
                audio_hash=digest,
                path=f"n/{i}.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=timezone.utc),
            ),
        )
    session.commit()
    return hashes


def test_absent_file_is_flagged_not_deleted(engine: Engine) -> None:
    """Deleting the row would destroy manually entered identifications."""
    with OrmSession(engine) as session:
        hashes = _add(session, 20)

        flagged = sweep_missing(session, set(hashes[1:]))
        session.commit()

        assert flagged == 1
        rows = session.scalars(select(Recording)).all()
        assert len(rows) == 20
        assert sum(1 for r in rows if r.missing_since is not None) == 1


def test_reappearing_file_clears_the_flag(engine: Engine) -> None:
    with OrmSession(engine) as session:
        hashes = _add(session, 20)
        sweep_missing(session, set(hashes[1:]))
        session.commit()

        sweep_missing(session, set(hashes))
        session.commit()

        assert all(r.missing_since is None for r in session.scalars(select(Recording)))


def test_mass_disappearance_is_refused(engine: Engine) -> None:
    """An unmounted archive must not flag the whole dataset."""
    with OrmSession(engine) as session:
        _add(session, 20)

        with pytest.raises(MassDisappearanceError) as excinfo:
            sweep_missing(session, set())

        assert excinfo.value.missing == 20
        assert all(r.missing_since is None for r in session.scalars(select(Recording)))


def test_threshold_boundary_is_allowed(engine: Engine) -> None:
    """Exactly 10% of 20 rows is 2 — at the threshold, not over it."""
    with OrmSession(engine) as session:
        hashes = _add(session, 20)

        assert sweep_missing(session, set(hashes[2:]), threshold=0.10) == 2


def test_empty_database_does_not_raise(engine: Engine) -> None:
    with OrmSession(engine) as session:
        assert sweep_missing(session, set()) == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_missing_sweep.py -v`
Expected: FAIL — `ImportError: cannot import name 'MassDisappearanceError'`.

- [ ] **Step 3: Implement**

Append to `src/fledermap/services/ingest.py`:

```python
DEFAULT_MISSING_THRESHOLD = 0.10


class MassDisappearanceError(Exception):
    """Too many recordings vanished at once to be believable.

    An unmounted archive or a mid-sync Syncthing makes every file look deleted.
    Flagging them all would be silent, wide damage, so the sweep refuses.
    """

    def __init__(self, missing: int, known: int) -> None:
        self.missing = missing
        self.known = known
        super().__init__(
            f"{missing} of {known} recordings absent — refusing to flag. "
            "Is the archive mounted and finished syncing?",
        )


def sweep_missing(
    session: OrmSession,
    seen_hashes: set[str],
    *,
    threshold: float = DEFAULT_MISSING_THRESHOLD,
) -> int:
    """Flag recordings whose source file was not seen. Never deletes rows."""
    known = session.scalars(select(Recording)).all()
    if not known:
        return 0

    absent = [r for r in known if r.audio_hash not in seen_hashes]
    if len(absent) > len(known) * threshold:
        raise MassDisappearanceError(missing=len(absent), known=len(known))

    now = datetime.now(tz=timezone.utc)
    flagged = 0
    for recording in known:
        if recording.audio_hash in seen_hashes:
            recording.missing_since = None
        elif recording.missing_since is None:
            recording.missing_since = now
            flagged += 1

    return flagged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_missing_sweep.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/ingest.py tests/test_missing_sweep.py
git commit -m "feat: flag missing recordings with a mass-disappearance guard"
```

---

### Task 13: CLI

Closes the phase: `fledermap ingest <dir>` end to end.

**Files:**
- Create: `src/fledermap/cli/__init__.py`, `src/fledermap/cli/main.py`, `src/fledermap/config.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `scan_with_skips` (8), `commit_scan`/`sweep_missing` (11, 12), `seed_taxonomy` (10), `make_engine` (9)
- Produces: `cli` — a `click.Group` with `ingest`; `Config` dataclass with `database_url: str`, `archive_root: Path`, `timestamp_source: str`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from fledermap.cli.main import cli
from tests.fixtures import build_wav, fmt_payload, wamd_payload

pytestmark = pytest.mark.db


def _archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive" / "Session_20130401_053030"
    root.mkdir(parents=True)
    for name, audio in (
        ("EPTSER_20150610_215446.wav", b"\x01\x02" * 32),
        ("MYODAU_20150623_213547.wav", b"\x09\x08" * 32),
    ):
        path = root / name
        path.write_bytes(
            build_wav(
                [
                    (b"fmt ", fmt_payload()),
                    (b"data", audio),
                    (b"wamd", wamd_payload(auto_id=name[:6])),
                ],
            ),
        )
        old = time.time() - 3600
        os.utime(path, (old, old))
    return tmp_path / "archive"


def test_ingest_reports_created_recordings(postgis_url: str, tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["ingest", str(archive)],
        env={"FLEDERMAP_DATABASE_URL": postgis_url},
    )

    assert result.exit_code == 0, result.output
    assert "created 2" in result.output


def test_second_run_creates_nothing(postgis_url: str, tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runner = CliRunner()
    env = {"FLEDERMAP_DATABASE_URL": postgis_url}

    runner.invoke(cli, ["ingest", str(archive)], env=env)
    result = runner.invoke(cli, ["ingest", str(archive)], env=env)

    assert result.exit_code == 0, result.output
    assert "created 0" in result.output
    assert "unchanged 2" in result.output


def test_missing_database_url_fails_clearly(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["ingest", str(_archive(tmp_path))], env={})

    assert result.exit_code != 0
    assert "FLEDERMAP_DATABASE_URL" in result.output
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.cli'`.

- [ ] **Step 3: Implement config**

`src/fledermap/config.py`:

```python
"""Runtime configuration.

`timestamp_source` defaults to "filename". This is a PROVISIONAL default, not a
settled decision (spec D17): the only evidence available is synthetic and
disagrees with itself by twelve hours. Revisit once real field recordings exist.
Changing it re-derives `recorded_at`; it does not require re-ingesting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fledermap.ingest.merge import TIMESTAMP_SOURCE_FILENAME

ENV_DATABASE_URL = "FLEDERMAP_DATABASE_URL"
ENV_TIMESTAMP_SOURCE = "FLEDERMAP_TIMESTAMP_SOURCE"


class ConfigError(Exception):
    """Required configuration is absent or invalid."""


@dataclass(frozen=True)
class Config:
    database_url: str
    archive_root: Path
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME

    @classmethod
    def from_env(cls, archive_root: Path) -> Config:
        url = os.environ.get(ENV_DATABASE_URL)
        if not url:
            msg = (
                f"{ENV_DATABASE_URL} is not set. Point it at Fledermap's own "
                "database (bats_db) — never at poiidx's, which drops and "
                "recreates its tables on any config change."
            )
            raise ConfigError(msg)
        return cls(
            database_url=url,
            archive_root=archive_root.resolve(),
            timestamp_source=os.environ.get(
                ENV_TIMESTAMP_SOURCE, TIMESTAMP_SOURCE_FILENAME,
            ),
        )
```

- [ ] **Step 4: Implement the CLI**

`src/fledermap/cli/__init__.py` — empty file.

`src/fledermap/cli/main.py`:

```python
"""Command line entry point."""

from __future__ import annotations

from pathlib import Path

import click
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config, ConfigError
from fledermap.domain.metadata import ScannedFile
from fledermap.ingest.scan import scan_with_skips
from fledermap.services.ingest import (
    MassDisappearanceError,
    commit_scan,
    sweep_missing,
)
from fledermap.store.db import create_all, make_engine
from fledermap.store.seed import seed_taxonomy


@click.group()
def cli() -> None:
    """Fledermap — organise bat recordings from handheld detectors."""


@cli.command()
@click.argument(
    "archive",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--sweep/--no-sweep",
    default=True,
    help="Flag recordings whose source file was not found.",
)
def ingest(archive: Path, sweep: bool) -> None:
    """Scan ARCHIVE and write recordings to the database. Read-only on ARCHIVE."""
    try:
        config = Config.from_env(archive)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    create_all(engine)

    seen: set[str] = set()
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        scanned = []
        skipped = 0
        for item in scan_with_skips(
            config.archive_root, timestamp_source=config.timestamp_source,
        ):
            if isinstance(item, ScannedFile):
                scanned.append(item)
                seen.add(item.audio_hash)
            else:
                skipped += 1

        report = commit_scan(session, scanned, archive_root=config.archive_root)
        session.commit()

        click.echo(
            f"created {report.created}  unchanged {report.unchanged}  "
            f"updated {report.updated}  moved {report.moved}  skipped {skipped}",
        )
        if report.unmapped_labels:
            labels = ", ".join(sorted(report.unmapped_labels))
            click.echo(f"unmapped labels needing review: {labels}")

        if sweep:
            try:
                flagged = sweep_missing(session, seen)
                session.commit()
                if flagged:
                    click.echo(f"flagged {flagged} recording(s) as missing")
            except MassDisappearanceError as exc:
                click.echo(f"WARNING: {exc}", err=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the whole suite and every gate**

```bash
hatch test
hatch run ruff:ruff check .
hatch run ruff:ruff format --check .
hatch run types:check
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/cli/ src/fledermap/config.py tests/test_cli.py
git commit -m "feat: add fledermap ingest command"
```

---

## Phase exit criteria

- [ ] `fledermap ingest <dir>` populates `bats_db` from a directory of EMT recordings.
- [ ] Running it twice reports `created 0`, and the database is unchanged.
- [ ] Renaming a file and re-running reports `moved`, not `created`.
- [ ] `test_metadata_change_does_not_change_hash` passes — spec D8 proven.
- [ ] `test_mass_disappearance_is_refused` passes.
- [ ] Recordings without GPS ingest successfully with `geom IS NULL`.
- [ ] `hatch test`, `ruff check`, `ruff format --check`, `types:check` all green.
- [ ] **HTMX tripwire check (spec §10): not applicable — this phase ships no UI.** State this explicitly in the phase review rather than skipping it.
