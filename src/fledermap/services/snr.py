"""Backfilling `Recording.snr_db` for recordings ingested before SNR
computation shipped (`fledermap backfill-snr`, mirrors `backfill-site-names`).
New recordings get it from `ingest/scan.py` directly; this only ever touches
rows where it's still `None`."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.ingest.snr import compute_snr_db
from fledermap.media.wav_pcm import UnreadableWavError, read_pcm
from fledermap.services.media import resolve_wav_path
from fledermap.store.models import Recording


def backfill_snr(session: OrmSession, archive_roots: tuple[Path, ...]) -> int:
    """Compute and store `snr_db` for every recording that doesn't have one
    yet. Returns the count updated. A recording whose file can't be read
    (missing, moved root, corrupt) is skipped, not counted -- the same "not
    every recording can be backfilled" acceptance `backfill_media` makes."""
    recordings = session.scalars(
        select(Recording).where(Recording.snr_db.is_(None)),
    ).all()

    updated = 0
    for recording in recordings:
        try:
            wav_path = resolve_wav_path(archive_roots, recording)
            samples, samplerate_hz, _full_scale = read_pcm(wav_path)
        except (FileNotFoundError, OSError, UnreadableWavError):
            continue
        recording.snr_db = compute_snr_db(samples, samplerate_hz)
        updated += 1

    return updated
