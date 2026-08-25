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
from fledermap.media.paths import PREVIEW_VERSION, preview_path, spectrogram_path
from fledermap.media.preview import make_preview
from fledermap.media.spectrogram import DEFAULT_SPECTROGRAM_PARAMS, render_spectrogram
from fledermap.store.models import Recording

app = make_job_app()

# Design spec §7 asks for "a small fixed retry count (e.g. 3, exponential
# backoff)". A bare `retry=3` resolves to `RetryStrategy(max_attempts=3)`
# with EVERY wait parameter left at 0, so all three attempts fire
# back-to-back -- no backoff at all, which is the opposite of what a retry is
# for when the cause is a transient resource problem. `exponential_wait=2`
# spaces them 2s, 4s, 8s apart (procrastinate's formula is
# `wait + linear_wait * attempts + exponential_wait ** (attempts + 1)`).
#
# `max_attempts` counts RETRIES, not runs: the job executes once, then up to
# 3 more times, and `procrastinate_jobs.attempts` lands on 4 when it finally
# fails. Asserted in `tests/test_jobs_tasks.py` so the off-by-one is pinned.
#
# `retry_exceptions` is deliberately NOT set: choosing which failures are
# worth retrying needs operational experience this project does not have yet,
# and retrying everything a small fixed number of times is the safe default
# until it does.
_RETRY = procrastinate.RetryStrategy(max_attempts=3, exponential_wait=2)


def spectrogram_lock_key(audio_hash: str) -> str:
    return f"spectrogram:{audio_hash}:{DEFAULT_SPECTROGRAM_PARAMS.params_hash}"


def preview_lock_key(audio_hash: str) -> str:
    return f"preview:{audio_hash}:{PREVIEW_VERSION}"


def _resolve_recording(session: OrmSession, audio_hash: str) -> Recording:
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one()
    if recording.missing_since is not None:
        msg = f"recording {audio_hash} has no source file (missing_since set)"
        raise FileNotFoundError(msg)
    return recording


@app.task(queue="media", pass_context=True, retry=_RETRY)
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

    out_path = spectrogram_path(media_root, audio_hash)
    render_spectrogram(wav_path, out_path, params=DEFAULT_SPECTROGRAM_PARAMS)


@app.task(queue="media", pass_context=True, retry=_RETRY)
def make_preview_task(context: procrastinate.JobContext, audio_hash: str) -> None:
    archive_root: Path = context.additional_context["archive_root"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = archive_root / recording.path

    out_path = preview_path(media_root, audio_hash)
    make_preview(wav_path, out_path)
