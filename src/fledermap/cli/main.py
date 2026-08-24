"""Command line entry point."""

from __future__ import annotations

from pathlib import Path

import click
from alembic.config import Config as AlembicConfig
from sqlalchemy.orm import Session as OrmSession

from alembic import command as alembic_command
from fledermap.config import Config, ConfigError
from fledermap.domain.metadata import ScannedFile
from fledermap.ingest.scan import INCOMPLETE_SCAN_REASONS, scan_with_skips
from fledermap.services.ingest import (
    IncompleteScanError,
    MassDisappearanceError,
    commit_scan,
    sweep_missing,
)
from fledermap.store.db import make_engine
from fledermap.store.seed import seed_taxonomy

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
