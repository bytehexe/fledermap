"""Task wrappers bridging `Recording` rows to `media/`'s pure functions.

This is the ONLY module that imports both `procrastinate` and the ORM models
-- `media/` stays pure (design spec §3). `app` here is the ONE Procrastinate
App for the whole process (design spec §6) -- constructed via
`jobs.app.make_job_app()` with no engine bound yet; every consumer (CLI
commands, tests) must `app.open(engine)` before deferring, or
`app.replace_connector(...)` before running a worker.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import procrastinate
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config
from fledermap.derive.sessions import partition_sessions
from fledermap.jobs.app import make_job_app
from fledermap.media.oscillogram import (
    DEFAULT_OSCILLOGRAM_PARAMS,
    render_oscillogram,
)
from fledermap.media.paths import (
    PREVIEW_VERSION,
    oscillogram_path,
    preview_path,
    spectrogram_path,
)
from fledermap.media.preview import make_preview
from fledermap.media.spectrogram import DEFAULT_SPECTROGRAM_PARAMS, render_spectrogram
from fledermap.services.derive import derive_sites
from fledermap.services.ingest import (
    IncompleteScanError,
    MassDisappearanceError,
    commit_scan,
    scan_all_roots,
    sweep_missing,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site
from fledermap.store.seed import seed_taxonomy

app = make_job_app()

logger = logging.getLogger(__name__)

# Shared by both scheduling paths onto the SAME job -- the periodic
# registration below, and Task 4's event-triggered `defer_async()` -- so
# `queueing_lock` coalesces a burst of either kind into at most one pending
# run, and `lock` keeps that run from ever overlapping one already executing
# (design spec §5, Global Constraints above).
_INGEST_CYCLE_LOCK = "ingest_cycle"
_INGEST_CYCLE_CRON = "*/5 * * * *"

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


def oscillogram_lock_key(audio_hash: str) -> str:
    return f"oscillogram:{audio_hash}:{DEFAULT_OSCILLOGRAM_PARAMS.params_hash}"


def preview_lock_key(audio_hash: str) -> str:
    return f"preview:{audio_hash}:{PREVIEW_VERSION}"


_NAME_SITE_LOCK = "poiidx-name-site"


def name_site_queueing_lock(cache_key: str, *, force: bool = False) -> str:
    """A forced refresh gets its own lock namespace -- otherwise it would
    collide with (and get silently dropped as an AlreadyEnqueued duplicate
    of) an already-pending normal job for the same coordinate, which is
    exactly the collision the plain lock relies on for legitimate
    derive_sites rebuilds of the SAME real site."""
    suffix = ":force" if force else ""
    return f"name_site:{cache_key}{suffix}"


@app.task(queue="geo", pass_context=True, retry=_RETRY)
def name_site_task(
    context: procrastinate.JobContext,
    site_id: int,
    force: bool = False,
) -> None:
    """Resolve one Site's name via poiidx, off the request path entirely
    (design spec Goals: "never a web handler"). `_NAME_SITE_LOCK` -- a
    single static value shared by every name_site job, applied at defer
    time by `enqueue_site_naming` -- serializes execution across all of
    them, so two never-before-touched-region downloads can never race each
    other (design spec §3's corrected performance note)."""
    # Local import: `site_naming` imports FROM this module at ITS top
    # level, so a top-level import here would be circular. Safe here
    # because by the time this function actually runs, module import has
    # long finished (same reasoning as `run_ingest_cycle`'s own local
    # `enqueue_media` import).
    from fledermap.services import site_naming

    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]
    if config.poiidx_database_url is None:
        # Can only happen if a job was deferred, then the config changed
        # before it ran -- nothing to do, and nothing to retry usefully.
        return
    site_naming.ensure_connected(config.poiidx_database_url)

    with OrmSession(engine) as session:
        site = session.get(Site, site_id)
        if site is None:
            # derive_sites rebuilt again since this job was enqueued and
            # this row no longer exists -- not an error.
            return
        point = decode_point(site.centroid)
        if point is None:
            return
        lon, lat = point
        resolved = site_naming.name_site(
            session,
            lon,
            lat,
            radius_m=config.site_naming_radius_m,
            site_radius_m=site.radius_m,
            force=force,
        )
        if resolved is not None:
            site.name, site.admin_path = resolved
        session.commit()


def _resolve_recording(session: OrmSession, audio_hash: str) -> Recording:
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one()
    if recording.missing_since is not None:
        msg = f"recording {audio_hash} has no source file (missing_since set)"
        raise FileNotFoundError(msg)
    return recording


def _resolve_wav_path(archive_roots: tuple[Path, ...], recording: Recording) -> Path:
    """`archive_root_index` out of range means a root list shrank after some
    recordings were tagged with a since-removed index -- fail clearly the
    same way `_resolve_recording` does above, rather than a bare `IndexError`
    (spec §3)."""
    try:
        root = archive_roots[recording.archive_root_index]
    except IndexError as exc:
        msg = (
            f"recording {recording.audio_hash} references archive_root_index "
            f"{recording.archive_root_index}, but only {len(archive_roots)} "
            f"root(s) are configured"
        )
        raise FileNotFoundError(msg) from exc
    return root / recording.path


@app.task(queue="media", pass_context=True, retry=_RETRY)
def render_spectrogram_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = _resolve_wav_path(archive_roots, recording)

    out_path = spectrogram_path(media_root, audio_hash)
    render_spectrogram(wav_path, out_path, params=DEFAULT_SPECTROGRAM_PARAMS)


@app.task(queue="media", pass_context=True, retry=_RETRY)
def render_oscillogram_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = _resolve_wav_path(archive_roots, recording)

    out_path = oscillogram_path(media_root, audio_hash)
    render_oscillogram(wav_path, out_path, params=DEFAULT_OSCILLOGRAM_PARAMS)


@app.task(queue="media", pass_context=True, retry=_RETRY)
def make_preview_task(context: procrastinate.JobContext, audio_hash: str) -> None:
    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = _resolve_wav_path(archive_roots, recording)

    out_path = preview_path(media_root, audio_hash)
    make_preview(wav_path, out_path)


@app.periodic(
    cron=_INGEST_CYCLE_CRON,
    lock=_INGEST_CYCLE_LOCK,
    queueing_lock=_INGEST_CYCLE_LOCK,
)
@app.task(queue="ingest", pass_context=True)
def run_ingest_cycle(context: procrastinate.JobContext, timestamp: int) -> None:
    """One full ingest+derive pass across every configured archive root.

    `timestamp` is unused directly -- Procrastinate's periodic-task machinery
    requires it as the first parameter (confirmed against
    `procrastinate/periodic.py`: `PeriodicDeferrer.defer_jobs` always injects
    it), and Task 4's manual `defer_async()` call supplies it too so both
    scheduling paths share one task signature.

    `MassDisappearanceError`/`IncompleteScanError` (the same two conditions
    `ingest`'s CLI turns into `EXIT_SWEEP_REFUSED`) are caught and logged --
    the job still "succeeds" from Procrastinate's point of view, so the next
    cycle (cron or event) retries automatically; there's no process to exit
    non-zero against any more (design spec §6). Anything else propagates:
    Procrastinate marks the job failed, subject to its own retry policy.
    """
    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]

    # Local import: `services.media` imports task objects FROM this module at
    # ITS top level, so a top-level import here would be circular (Global
    # Constraints above). Safe here because by the time this function
    # actually runs, module import has long finished.
    from fledermap.services.media import enqueue_media

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        scanned, seen, skipped, incomplete_skips = scan_all_roots(
            config.archive_roots,
            timestamp_source=config.timestamp_source,
            default_timezone=config.default_timezone,
        )
        report = commit_scan(session, scanned, archive_roots=config.archive_roots)
        session.commit()
        enqueue_media(report.created_hashes, engine)

        ingest_summary = (
            f"ingest cycle: created {report.created} unchanged {report.unchanged} "
            f"updated {report.updated} moved {report.moved} "
            f"replaced {report.replaced} duplicates {report.duplicates} "
            f"skipped {skipped} identifications added "
            f"{report.identifications_added} superseded "
            f"{report.identifications_superseded}"
        )

        # A refused sweep (spec §10 decision 2) must NOT skip derive: it's
        # idempotent and cheap, and skipping it would let a single
        # permanently-unreadable file (UNREADABLE/UNPARSEABLE are both
        # permanent INCOMPLETE_SCAN_REASONS) silently wedge session/site
        # derivation forever, with only a 5-minutely error log to explain why
        # new recordings stop appearing on the map. `flagged` stays `None`
        # when the sweep was refused so the summary line below can say so
        # explicitly, rather than reporting a misleading `0` as if the sweep
        # had actually run and found nothing missing.
        flagged: int | None
        try:
            flagged = sweep_missing(session, seen, skipped=incomplete_skips)
            session.commit()
        except (MassDisappearanceError, IncompleteScanError) as exc:
            flagged = None
            logger.error("%s -- %s", ingest_summary, exc)

        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
        )
        session.commit()
        site_report = derive_sites(
            session,
            eps_m=config.site_eps_m,
            min_points=config.site_min_points,
        )
        session.commit()

        from fledermap.services.site_naming import enqueue_site_naming

        enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=config.poiidx_database_url,
        )
        session.commit()

        logger.info(
            "%s flagged_missing %s -- sessions created %d extended %d "
            "merge_proposals %d -- sites %d unclustered %d",
            ingest_summary,
            "refused" if flagged is None else flagged,
            session_report.created,
            session_report.extended,
            session_report.merge_proposals,
            site_report.site_count,
            site_report.unclustered,
        )
