"""Command line entry point."""

from __future__ import annotations

import asyncio
import importlib.resources
import logging
import shutil
import subprocess
import sys
import time
import urllib.error
from datetime import timedelta
from pathlib import Path

import click
import procrastinate
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config, ConfigError, resolve_static_root
from fledermap.derive.sessions import partition_sessions
from fledermap.jobs.app import (
    ensure_schema,
    make_worker_connector,
    requeue_stalled_jobs,
)
from fledermap.jobs.tasks import _INGEST_CYCLE_LOCK, run_ingest_cycle
from fledermap.jobs.tasks import app as jobs_app
from fledermap.jobs.watch import start_watching
from fledermap.services.derive import derive_sites
from fledermap.services.ingest import (
    IncompleteScanError,
    MassDisappearanceError,
    commit_scan,
    reresolve_unmapped_identifications,
    scan_all_roots,
    sweep_missing,
)
from fledermap.services.media import backfill_media, enqueue_media
from fledermap.services.site_naming import enqueue_site_naming
from fledermap.services.systemd_install import render_unit_files, systemd_user_dir
from fledermap.services.vendor_assets import (
    ASSETS,
    IntegrityError,
    ensure_vendor_assets,
)
from fledermap.services.vendor_assets import fetch_all as fetch_all_vendor_assets
from fledermap.store.db import make_engine
from fledermap.store.seed import seed_taxonomy
from fledermap.web.app import create_app

logger = logging.getLogger(__name__)

# Distinct from ConfigError's exit code (1, via click.ClickException) AND from
# Click's OWN reserved exit code 2 (click.exceptions.UsageError — raised for
# an unrecognised option or bad option value, since `--sweep/--no-sweep`
# goes through Click's own parsing). Reusing 2 would make "you mistyped an
# option, nothing happened" and "ingest succeeded, sweep refused"
# indistinguishable to a caller checking only the exit code — defeating the
# reason this has its own code at all. Picked 3 specifically to stay clear of
# Click's reserved 0/1/2 (confirmed via
# `click.exceptions.ClickException.exit_code == 1` and
# `UsageError.exit_code == 2` against the installed version).
#
# The ingest itself succeeded and was committed when this fires — only the
# missing-file sweep was refused. That is a real operational event a cron job
# or monitoring script should be able to notice without parsing stdout, so it
# gets its own nonzero exit code rather than folding into exit 0. Stated in
# `ingest`'s docstring too, so it surfaces in `--help` rather than living only
# in this comment. See task-13 report, judgement call, for the full reasoning.
EXIT_SWEEP_REFUSED = 3


def _alembic_script_location() -> Path:
    """Where the packaged Alembic migration scripts live -- resolved via
    `importlib.resources` against the INSTALLED `fledermap` package, not a
    hardcoded directory-depth guess relative to this file.

    A prior version computed this as
    `Path(__file__).resolve().parents[3] / "alembic"`, which only landed on
    the repo root for a dev/editable install (`hatch run`, where this file
    lives at `<repo>/src/fledermap/cli/main.py`). Under a REAL install (pipx,
    `pip install`), there is no `src/` layer -- this file ends up at
    `.../site-packages/fledermap/cli/main.py` -- so that guess pointed at a
    nonexistent directory, and every command that touches the database
    (`ingest`, `serve`, `worker`, ...) failed with
    `alembic.util.exc.CommandError: Path doesn't exist`.

    The scripts now live at `src/fledermap/alembic/` -- a real subdirectory
    of the `fledermap` package itself, not a sibling `alembic/` at the repo
    root -- specifically so hatchling's default src-layout packaging (which
    already ships everything under `src/fledermap/**`, no extra config
    needed) includes them in the built wheel automatically.
    `importlib.resources.files("fledermap")` then resolves correctly no
    matter how `fledermap` got onto this machine: it points at
    `src/fledermap` for an editable dev install (confirmed: this is a real
    on-disk directory, not a build artifact, so the migration scripts are
    visible immediately with no rebuild step) and at the real installed
    package directory for pipx/pip. One code path, no install-mode branching.
    `tests/test_packaging.py` proves the scripts actually ship in a built
    wheel; this function's own test proves it resolves to a real directory
    with real scripts in dev-checkout form.
    """
    return Path(str(importlib.resources.files("fledermap"))) / "alembic"


def _run_migrations(database_url: str) -> None:
    """Build (or update) the schema via the real Alembic migration.

    NOT `create_all` — `store/db.py`'s own docstring says `create_all` is test
    and development convenience only, and production schema comes from
    Alembic. This is the one place a real user's database gets built, so it
    must be the one place that honours that.

    Deliberately NOT `AlembicConfig("alembic.ini")`: env.py calls fileConfig()
    whenever the config carries a file name, and fileConfig defaults to
    disable_existing_loggers=True — which would silently disable every logger
    already configured in the process. `script_location` and `sqlalchemy.url`
    are the only settings `upgrade` needs (same pattern as
    `tests/test_migrations.py`'s `migrated_engine` fixture).
    """
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_alembic_script_location()))
    cfg.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(cfg, "head")


def _require_archive_roots_exist(archive_roots: tuple[Path, ...]) -> None:
    """`_parse_archive_roots` (config.py) never checks a root exists or is a
    directory -- the old single-root CLI arg used `click.Path(exists=True,
    file_okay=False)`, which is gone. Without this, `worker` on a bad/
    unmounted root dies with a raw unhandled `OSError(ENOTDIR)` traceback
    from watchdog's `Observer.start()` instead of a clean `ClickException`,
    and an existing-but-empty mountpoint (not actually mounted) yields zero
    files AND zero skips, so `sweep_missing` (global across all roots) can
    silently flag every recording from that root as missing, unattended,
    every 5 minutes.

    Called only from `ingest`/`worker` -- NOT from `Config.from_env()`
    itself, and NOT from `derive`/`serve`/`enqueue-media`, which also build a
    `Config` but never touch the archive; a global check there would break
    headless web-only deployments that never mount it."""
    missing = [root for root in archive_roots if not root.is_dir()]
    if missing:
        listed = ", ".join(str(p) for p in missing)
        msg = f"archive root(s) do not exist or are not directories: {listed}"
        raise click.ClickException(msg)


def _fetch_missing_vendor_assets_or_die(vendor_dir: Path) -> None:
    """`serve`'s automatic cache-warming: fetches only what's missing (a
    warm cache costs nothing here -- see `ensure_vendor_assets`), and turns
    a network/integrity failure into a clean `ClickException` instead of a
    raw traceback, since this now runs on every `serve` startup rather than
    only when a human deliberately invokes a fetch script."""
    try:
        fetched = ensure_vendor_assets(vendor_dir)
    except (IntegrityError, urllib.error.URLError, OSError) as exc:
        msg = (
            f"could not fetch vendor assets into {vendor_dir}: {exc}. Run "
            "`fledermap fetch-assets` once real network access is available, "
            "or pre-warm this directory on another machine and copy it over."
        )
        raise click.ClickException(msg) from exc
    if fetched:
        click.echo(f"fetched {len(fetched)} vendor asset(s) into {vendor_dir}")


@click.group()
def cli() -> None:
    """Fledermap — organise bat recordings from handheld detectors."""


@cli.command()
@click.option(
    "--sweep/--no-sweep",
    default=True,
    help="Flag recordings whose source file was not found.",
)
@click.pass_context
def ingest(ctx: click.Context, sweep: bool) -> None:
    """Scan every configured archive root and write recordings to the
    database. Read-only on every root (D16).

    Exit codes: 0 on success. 1 if configuration is invalid (nothing was
    written). 3 if the ingest itself succeeded and was committed, but the
    missing-file sweep was refused -- check the warning on stderr for which.
    """
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    _require_archive_roots_exist(config.archive_roots)

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        reresolve_unmapped_identifications(session)
        session.commit()

        scanned, seen, skipped, incomplete_skips = scan_all_roots(
            config.archive_roots,
            timestamp_source=config.timestamp_source,
            default_timezone=config.default_timezone,
        )
        report = commit_scan(session, scanned, archive_roots=config.archive_roots)
        session.commit()

        enqueue_media(report.created_hashes, engine)

        click.echo(
            f"created {report.created}  unchanged {report.unchanged}  "
            f"updated {report.updated}  moved {report.moved}  "
            f"replaced {report.replaced}  duplicates {report.duplicates}  "
            f"skipped {skipped}",
        )
        click.echo(
            f"identifications added {report.identifications_added}  "
            f"superseded {report.identifications_superseded}",
        )
        if report.unmapped_labels:
            labels = ", ".join(sorted(report.unmapped_labels))
            click.echo(f"unmapped labels needing review: {labels}")

        if sweep:
            try:
                flagged = sweep_missing(session, seen, skipped=incomplete_skips)
                session.commit()
                if flagged:
                    click.echo(f"flagged {flagged} recording(s) as missing")
            except MassDisappearanceError as exc:
                click.echo(f"WARNING: {exc}", err=True)
                ctx.exit(EXIT_SWEEP_REFUSED)
            except IncompleteScanError as exc:
                click.echo(f"WARNING: {exc}", err=True)
                ctx.exit(EXIT_SWEEP_REFUSED)


@cli.command()
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Re-resolve site names from poiidx even where SiteNameCache already "
        "has an answer for that coordinate -- for a poiidx_filter_config.yaml "
        "change or a poiidx reindex whose effect the cache would otherwise "
        "hide forever, since derive_sites resets Site.name but never touches "
        "SiteNameCache."
    ),
)
def derive(force: bool) -> None:
    """Partition sessions and rebuild sites from what `ingest` has stored.

    Headless — no web. Site naming is enqueued (poiidx jobs, resolved off
    this process) when `FLEDERMAP_POIIDX_DATABASE_URL` is configured; a
    no-op otherwise. Safe to re-run at any time: session partitioning only
    touches unsessioned recordings, and site rebuilding is idempotent.
    """
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
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

        named_count = enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=config.poiidx_database_url,
            force=force,
        )
        session.commit()

        click.echo(
            f"sessions: created {session_report.created}  "
            f"extended {session_report.extended}  "
            f"merge proposals {session_report.merge_proposals}",
        )
        click.echo(
            f"sites: {site_report.site_count}  unclustered {site_report.unclustered}  "
            f"naming jobs enqueued {named_count}",
        )


async def _defer_ingest_cycle() -> None:
    try:
        await run_ingest_cycle.configure(
            lock=_INGEST_CYCLE_LOCK,
            queueing_lock=_INGEST_CYCLE_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except procrastinate.exceptions.AlreadyEnqueued:
        pass  # a cycle is already queued behind the one currently running
    except Exception:
        # `jobs/watch.py`'s `_Debouncer._fire()` schedules this coroutine via
        # `asyncio.ensure_future(...)` with no kept reference -- an
        # unhandled exception here would otherwise vanish silently into an
        # "Task exception was never retrieved" warning instead of surfacing
        # anywhere an operator could see it (a connection error during
        # `.defer_async(...)`, for instance).
        logger.exception("failed to defer an ingest cycle from the watcher")


async def _run_worker_async(config: Config, engine: Engine, *, wait: bool) -> None:
    async_connector = make_worker_connector(config.database_url)
    with jobs_app.replace_connector(async_connector) as worker_app:
        # `run_worker` (sync) opens the app itself via `async with
        # self.open_async(): await self.run_worker_async(...)` (confirmed in
        # procrastinate/app.py) before running -- `run_worker_async` does NOT
        # do this on its own, so it must be opened explicitly here too.
        # Without it, `run_worker_async` raises `AppNotOpen` immediately.
        async with worker_app.open_async():
            # Recover anything a previous, now-dead worker process left
            # stuck in `doing` -- see `requeue_stalled_jobs`'s docstring.
            # Before starting the watcher/worker loop: a job still stuck
            # under a lock (e.g. `_INGEST_CYCLE_LOCK`) would otherwise
            # silently block every cron tick and watchdog event this
            # process ever tries for that lock.
            await requeue_stalled_jobs(worker_app)

            loop = asyncio.get_running_loop()
            observer = start_watching(config.archive_roots, loop, _defer_ingest_cycle)
            try:
                await worker_app.run_worker_async(
                    wait=wait,
                    install_signal_handlers=wait,
                    listen_notify=wait,
                    additional_context={
                        "archive_roots": config.archive_roots,
                        "media_root": config.media_root,
                        "config": config,
                        "engine": engine,
                    },
                )
            finally:
                observer.stop()
                observer.join()


@cli.command()
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Keep running until stopped (default), or process the current "
    "queue once and exit.",
)
def worker(wait: bool) -> None:
    """Run the media job worker AND the continuous ingest+derive watcher
    (design spec 2026-08-28-fledermap-phase6-watcher-design.md): a cron
    backstop plus a debounced filesystem watch, both deferring the same
    `run_ingest_cycle` task. Reads FLEDERMAP_ARCHIVE_ROOTS to resolve
    `Recording.path` AND to know which directories to watch.
    """
    logging.basicConfig(level=logging.INFO)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    _require_archive_roots_exist(config.archive_roots)

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    asyncio.run(_run_worker_async(config, engine, wait=wait))


@cli.command()
@click.option(
    "--host",
    default=None,
    help="Interface to bind. Defaults to FLEDERMAP_HOST, then the config "
    "file's 'host' setting, then 127.0.0.1 -- see Config.host.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Port to listen on. Defaults to FLEDERMAP_PORT, then the config "
    "file's 'port' setting, then 5000 -- see Config.port.",
)
def serve(host: str | None, port: int | None) -> None:
    """Run the web map. Reads FLEDERMAP_DATABASE_URL, FLEDERMAP_MEDIA_ROOT,
    and FLEDERMAP_ARCHIVE_ROOTS (all required) and, optionally,
    FLEDERMAP_STATIC_ROOT, FLEDERMAP_HOST, and FLEDERMAP_PORT.
    FLEDERMAP_ARCHIVE_ROOTS is used by the recording-details page's
    detail-image routes, which render straight from the source WAV on every
    request rather than a cached file. Vendor JS/CSS (Leaflet, HTMX, Alpine)
    are fetched into FLEDERMAP_STATIC_ROOT automatically on first run, or
    whenever the cache is missing something -- see `fetch-assets` to
    pre-warm that cache (e.g. for an offline install) instead of fetching it
    at server startup.
    """
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    _fetch_missing_vendor_assets_or_die(config.static_root / "vendor")

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    app = create_app(
        engine,
        config.static_root,
        config.media_root,
        config.archive_roots,
    )
    app.run(
        host=host if host is not None else config.host,
        port=port if port is not None else config.port,
    )


@cli.command(name="fetch-assets")
def fetch_assets() -> None:
    """Fetch vendor JS/CSS (Leaflet, HTMX, Alpine) into FLEDERMAP_STATIC_ROOT.

    `serve` already does this automatically for whatever's missing, so this
    command is only for pre-warming the cache deliberately -- ahead of an
    offline/air-gapped deployment, or to force a full re-fetch and overwrite
    of everything, verified, even what's already present.

    Uses `resolve_static_root()` directly, not `Config.from_env` -- this
    command touches nothing but the static/vendor cache, so it has no
    business demanding FLEDERMAP_DATABASE_URL/FLEDERMAP_MEDIA_ROOT the way
    every other command here does.
    """
    vendor_dir = resolve_static_root() / "vendor"
    try:
        fetch_all_vendor_assets(vendor_dir)
    except (IntegrityError, urllib.error.URLError, OSError) as exc:
        msg = f"could not fetch vendor assets into {vendor_dir}: {exc}"
        raise click.ClickException(msg) from exc
    click.echo(f"fetched {len(ASSETS)} vendor asset(s) into {vendor_dir}")


@cli.command(name="enqueue-media")
def enqueue_media_command() -> None:
    """Backfill media jobs for recordings with nothing on disk yet -- for
    recordings ingested before this phase existed, or after a media-params
    change that invalidates old renders."""
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    # `backfill_media` -> `enqueue_media` opens `jobs_app` itself.
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        count = backfill_media(session, config.media_root)
        session.commit()

    click.echo(f"enqueued {count}")


@cli.command(name="backfill-site-names")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Re-resolve site names from poiidx even where SiteNameCache already "
        "has an answer for that coordinate -- for a poiidx_filter_config.yaml "
        "change or a poiidx reindex whose effect the cache would otherwise "
        "hide forever."
    ),
)
def backfill_site_names_command(force: bool) -> None:
    """Resolve names for any Site still missing one via poiidx -- for sites
    that predate this feature, or whose name_site job failed past its retry
    budget. A no-op (reports "enqueued 0") if FLEDERMAP_POIIDX_DATABASE_URL
    isn't configured."""
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        count = enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=config.poiidx_database_url,
            force=force,
        )
        session.commit()

    click.echo(f"enqueued {count}")


@cli.command()
@click.option(
    "--restart",
    is_flag=True,
    help=(
        "Also restart fledermap.target now, so an already-running serve/"
        "worker picks up a new install path immediately (e.g. after a pipx "
        "upgrade). Without this flag, `enable --now` only starts units that "
        "aren't already active, so an in-place upgrade needs a manual "
        "`systemctl --user restart fledermap.target` -- --restart does that "
        "for you."
    ),
)
def install(*, restart: bool) -> None:
    """Generate and enable systemd --user units for `serve` + `worker`, so
    both survive logout/reboot without a terminal open. Linux only (systemd
    --user has no equivalent elsewhere); assumes an already-installed
    `fledermap` (pipx, pip, ...) is on PATH -- a dev checkout run via `hatch
    run` isn't a sensible target for a persistent background service (its
    env's path isn't stable across `hatch env remove`), so this makes no
    attempt to detect or support that case.

    Safe to re-run (e.g. after a pipx upgrade moves the venv) -- unit files
    are overwritten unconditionally. Note this does NOT restart an
    already-running serve/worker: `enable --now` only starts units that
    aren't already active, so an in-place upgrade needs a manual
    `systemctl --user restart fledermap.target` to pick up the new path --
    pass --restart to have this command do that for you.
    """
    if sys.platform != "linux":
        raise click.ClickException(
            "fledermap install only supports Linux (systemd --user).",
        )

    exe = shutil.which("fledermap")
    if exe is None:
        raise click.ClickException(
            "fledermap not found on PATH -- install it first "
            "(e.g. `pipx install fledermap`, or `pip install .`).",
        )

    unit_dir = systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in render_unit_files(exe).items():
        (unit_dir / filename).write_text(content)

    systemctl_calls = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "fledermap.target"],
    ]
    if restart:
        systemctl_calls.append(["systemctl", "--user", "restart", "fledermap.target"])

    for systemctl_args in systemctl_calls:
        try:
            subprocess.run(systemctl_args, check=True)
        except FileNotFoundError as exc:
            msg = (
                "systemctl not found -- fledermap install needs a systemd "
                "user session (`systemctl --user ...`)."
            )
            raise click.ClickException(msg) from exc
        except subprocess.CalledProcessError as exc:
            msg = f"`{' '.join(systemctl_args)}` failed: {exc}"
            raise click.ClickException(msg) from exc

    click.echo(f"Installed systemd --user units to {unit_dir}")
    click.echo(
        "Manage with: systemctl --user {status,restart,stop} fledermap.target",
    )
