"""Task wrappers bridging `Recording` rows to `media/`'s pure functions.

This is the ONLY module that imports both `procrastinate` and the ORM models
-- `media/` stays pure (design spec §3). `app` here is the ONE Procrastinate
App for the whole process (design spec §6) -- constructed via
`jobs.app.make_job_app()` with no engine bound yet; every consumer (CLI
commands, tests) must `app.open(engine)` before deferring, or
`app.replace_connector(...)` before running a worker.
"""

from __future__ import annotations

from pathlib import Path

import procrastinate
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import make_job_app
from fledermap.media.preview import make_preview
from fledermap.media.spectrogram import SpectrogramParams, render_spectrogram
from fledermap.store.models import Recording

app = make_job_app()

_SPECTROGRAM_PARAMS = SpectrogramParams()


def spectrogram_lock_key(audio_hash: str) -> str:
    return f"spectrogram:{audio_hash}:{_SPECTROGRAM_PARAMS.params_hash}"


def preview_lock_key(audio_hash: str) -> str:
    # Fixed literal, not a computed hash: the x10 ratio is fixed by spec and
    # not exposed as a v1 setting (design spec §5).
    return f"preview:{audio_hash}:v1"


def _resolve_recording(session: OrmSession, audio_hash: str) -> Recording:
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one()
    if recording.missing_since is not None:
        msg = f"recording {audio_hash} has no source file (missing_since set)"
        raise FileNotFoundError(msg)
    return recording


@app.task(queue="media", pass_context=True, retry=3)
def render_spectrogram_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    archive_root: Path = context.additional_context["archive_root"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = archive_root / recording.path

    out_path = (
        media_root
        / audio_hash[:2]
        / audio_hash
        / f"spectrogram-{_SPECTROGRAM_PARAMS.params_hash}.webp"
    )
    render_spectrogram(wav_path, out_path, params=_SPECTROGRAM_PARAMS)


@app.task(queue="media", pass_context=True, retry=3)
def make_preview_task(context: procrastinate.JobContext, audio_hash: str) -> None:
    archive_root: Path = context.additional_context["archive_root"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = archive_root / recording.path

    out_path = media_root / audio_hash[:2] / audio_hash / "preview-v1.opus"
    make_preview(wav_path, out_path)
