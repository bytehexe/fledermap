"""Where derived media lives under the media root.

The single definition of the on-disk layout
`<media_root>/<hash[:2]>/<hash>/<artefact>`. It lives in `media/` because it
is pure path arithmetic -- no DB and no queue awareness, so it does not
breach that package's boundary (design spec §3) -- and because both callers
already depend on `media/` anyway.

Having one definition matters more here than the duplication is long. The
writers (`jobs/tasks.py`) and the reader that decides whether work is still
needed (`services/media.py`'s `_has_media`) must agree EXACTLY: if the two
formulas ever drift, `backfill_media` either re-enqueues every recording
forever or never enqueues anything, and no test notices, because a test that
hand-builds the same string is drifting right along with one of them.
"""

from __future__ import annotations

from pathlib import Path

from fledermap.media.oscillogram import DEFAULT_OSCILLOGRAM_PARAMS, OscillogramParams
from fledermap.media.spectrogram import DEFAULT_SPECTROGRAM_PARAMS, SpectrogramParams

# The preview format's version, which is part of BOTH its filename and its
# Procrastinate lock key. A fixed literal rather than a computed hash: the
# x10 time-expansion ratio is fixed by spec and not exposed as a v1 setting
# (design spec §5), so there are no parameters to hash. Bump it when the
# preview's format or ratio changes, to invalidate existing renders.
PREVIEW_VERSION = "v1"


def recording_media_dir(media_root: Path, audio_hash: str) -> Path:
    """The per-recording directory. The `audio_hash[:2]` shard keeps any one
    directory from accumulating every recording in the archive."""
    return media_root / audio_hash[:2] / audio_hash


def spectrogram_path(
    media_root: Path,
    audio_hash: str,
    params: SpectrogramParams = DEFAULT_SPECTROGRAM_PARAMS,
) -> Path:
    """Where `params`' spectrogram for `audio_hash` belongs. `params_hash` is
    in the filename so a settings change invalidates old renders without
    touching `audio_hash` or requiring a migration."""
    return (
        recording_media_dir(media_root, audio_hash)
        / f"spectrogram-{params.params_hash}.webp"
    )


def oscillogram_path(
    media_root: Path,
    audio_hash: str,
    params: OscillogramParams = DEFAULT_OSCILLOGRAM_PARAMS,
) -> Path:
    """Where `params`' oscillogram for `audio_hash` belongs -- same
    hash-in-filename cache-invalidation shape as `spectrogram_path`."""
    return (
        recording_media_dir(media_root, audio_hash)
        / f"oscillogram-{params.params_hash}.webp"
    )


def preview_path(media_root: Path, audio_hash: str) -> Path:
    """Where `audio_hash`'s time-expanded preview belongs."""
    return (
        recording_media_dir(media_root, audio_hash) / f"preview-{PREVIEW_VERSION}.opus"
    )
