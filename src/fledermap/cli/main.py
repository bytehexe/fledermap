"""Command line entry point."""

from __future__ import annotations

import logging
import urllib.error
from datetime import timedelta
from pathlib import Path

import click
from alembic.config import Config as AlembicConfig
from sqlalchemy.orm import Session as OrmSession

from alembic import command as alembic_command
from fledermap.config import Config, ConfigError, resolve_static_root
from fledermap.derive.sessions import partition_sessions
from fledermap.domain.metadata import ScannedFile
from fledermap.ingest.scan import INCOMPLETE_SCAN_REASONS, scan_with_skips
from fledermap.jobs.app import ensure_schema, make_worker_connector
from fledermap.jobs.tasks import app as jobs_app
from fledermap.services.derive import derive_sites
from fledermap.services.ingest import (
    IncompleteScanError,
    MassDisappearanceError,
    commit_scan,
    sweep_missing,
)
from fledermap.services.media import backfill_media, enqueue_media
from fledermap.services.vendor_assets import (
    ASSETS,
    IntegrityError,
    ensure_vendor_assets,
)
from fledermap.services.vendor_assets import fetch_all as fetch_all_vendor_assets
from fledermap.store.db import make_engine
from fledermap.store.seed import seed_taxonomy
from fledermap.web.app import create_app

# Repo root: src/fledermap/cli/main.py -> cli -> fledermap -> src -> repo root.
# `alembic/` lives at the repo root, not inside the installed package, mirroring
# the layout `tests/test_migrations.py` already assumes.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Distinct from ConfigError's exit code (1, via click.ClickException) AND from
# Click's OWN reserved exit code 2 (click.exceptions.UsageError — raised for a
# nonexistent ARCHIVE path, a missing argument, or an unrecognised option,
# since `--sweep/--no-sweep` and the `ARCHIVE` argument both go through
# Click's own parsing). Reusing 2 would make "you mistyped the path, nothing
# happened" and "ingest succeeded, sweep refused" indistinguishable to a
# caller checking only the exit code — defeating the reason this has its own
# code at all. Picked 3 specifically to stay clear of Click's reserved 0/1/2
# (confirmed via `click.exceptions.ClickException.exit_code == 1` and
# `UsageError.exit_code == 2` against the installed version).
#
# The ingest itself succeeded and was committed when this fires — only the
# missing-file sweep was refused. That is a real operational event a cron job
# or monitoring script should be able to notice without parsing stdout, so it
# gets its own nonzero exit code rather than folding into exit 0. Stated in
# `ingest`'s docstring too, so it surfaces in `--help` rather than living only
# in this comment. See task-13 report, judgement call, for the full reasoning.
EXIT_SWEEP_REFUSED = 3


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
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(cfg, "head")


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
@click.argument(
    "archive",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--sweep/--no-sweep",
    default=True,
    help="Flag recordings whose source file was not found.",
)
@click.pass_context
def ingest(ctx: click.Context, archive: Path, sweep: bool) -> None:
    """Scan ARCHIVE and write recordings to the database. Read-only on ARCHIVE.

    Exit codes: 0 on success. 1 if configuration is invalid (nothing was
    written). 3 if the ingest itself succeeded and was committed, but the
    missing-file sweep was refused (too many recordings vanished at once, or
    some files were still settling) — check the warning on stderr for which.
    """
    try:
        config = Config.from_env(archive)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    # No `jobs_app.open(engine)` here: `enqueue_media` opens it itself, by
    # contract. `ensure_schema` uses `engine` directly and does not need an
    # opened connector either.
    ensure_schema(jobs_app, engine)

    seen: set[str] = set()
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        scanned = []
        skipped = 0
        incomplete_skips = 0
        for item in scan_with_skips(
            config.archive_root,
            timestamp_source=config.timestamp_source,
            default_timezone=config.default_timezone,
        ):
            if isinstance(item, ScannedFile):
                scanned.append(item)
                seen.add(item.audio_hash)
            else:
                _, reason = item
                skipped += 1
                if reason in INCOMPLETE_SCAN_REASONS:
                    incomplete_skips += 1

        report = commit_scan(session, scanned, archive_root=config.archive_root)
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
def derive() -> None:
    """Partition sessions and rebuild sites from what `ingest` has stored.

    Headless — no web, no site naming (that's a later phase's job queue).
    Safe to re-run at any time: session partitioning only touches unsessioned
    recordings, and site rebuilding is idempotent.
    """
    try:
        config = Config.from_env(Path.cwd())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)

    with OrmSession(engine) as session:
        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
            transect_distance_m=config.transect_distance_m,
        )
        session.commit()

        site_report = derive_sites(
            session,
            eps_m=config.site_eps_m,
            min_points=config.site_min_points,
        )
        session.commit()

        click.echo(
            f"sessions: created {session_report.created}  "
            f"extended {session_report.extended}  "
            f"merge proposals {session_report.merge_proposals}",
        )
        click.echo(
            f"sites: {site_report.site_count}  unclustered {site_report.unclustered}",
        )


@cli.command()
@click.argument(
    "archive",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Keep running until stopped (default), or process the current "
    "queue once and exit.",
)
def worker(archive: Path, wait: bool) -> None:
    """Run the media job worker. Reads and writes files under ARCHIVE and
    the configured media root; requires the same ARCHIVE path `ingest` uses
    to resolve `Recording.path` to a real file.
    """
    # Procrastinate logs worker startup and every per-job event at INFO. With
    # no handler and the root logger at its WARNING default, a long-lived
    # `worker` daemon is completely silent until something crashes. Scoped to
    # this command deliberately: `ingest`/`derive` are short-lived, report
    # through `click.echo`, and were not asked to change their output.
    logging.basicConfig(level=logging.INFO)

    try:
        config = Config.from_env(archive)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    async_connector = make_worker_connector(config.database_url)
    with jobs_app.replace_connector(async_connector) as worker_app:
        worker_app.run_worker(
            wait=wait,
            install_signal_handlers=wait,
            listen_notify=wait,
            additional_context={
                "archive_root": config.archive_root,
                "media_root": config.media_root,
                "engine": engine,
            },
        )


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
    """Run the web map. Reads FLEDERMAP_DATABASE_URL and FLEDERMAP_MEDIA_ROOT
    (both required) and, optionally, FLEDERMAP_STATIC_ROOT, FLEDERMAP_HOST,
    and FLEDERMAP_PORT. Vendor JS/CSS (Leaflet, HTMX, Alpine) are fetched
    into FLEDERMAP_STATIC_ROOT automatically on first run, or whenever the
    cache is missing something -- see `fetch-assets` to pre-warm that cache
    (e.g. for an offline install) instead of fetching it at server startup.
    """
    try:
        config = Config.from_env(Path.cwd())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    _fetch_missing_vendor_assets_or_die(config.static_root / "vendor")

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    app = create_app(
        engine,
        config.static_root,
        config.media_root,
        transect_distance_m=config.transect_distance_m,
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
        config = Config.from_env(Path.cwd())
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
