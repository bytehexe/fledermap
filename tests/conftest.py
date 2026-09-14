from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import platformdirs
import pytest
from sqlalchemy import Engine, text
from testcontainers.community.postgres import PostgresContainer

from fledermap.config import ENV_CONFIG_FILE
from fledermap.services.vendor_assets import ASSETS
from fledermap.store import db
from fledermap.store.db import make_engine
from fledermap.store.models import Base


@pytest.fixture
def _vendor_icons(tmp_path: Path) -> None:
    """Every view test builds `create_app(engine, tmp_path / "static", ...)` against a fresh,
    empty `tmp_path` -- nothing in this suite calls the network-fetching `ensure_vendor_assets`
    (see `tests/test_cli.py`'s `_populate_vendor_cache`, which writes empty placeholder bytes,
    not real content). That's fine for JS/CSS, which no test reads the content of, but
    `web/icons.py`'s `icon()` Jinja global reads a real SVG file off disk and raises
    `IconNotFoundError` loudly if it's missing -- so any test that renders a template calling
    `icon()` needs one there first.

    Centralized here (2026-09-13, Task 4 of the icon-set migration) rather than duplicated per
    test file: writes a minimal but real-Tabler-shaped SVG (same `class="icon icon-tabler
    icons-tabler-<style> icon-tabler-<name>"` convention `web/icons.py` documents) for every
    icon `vendor_assets.ASSETS` entry, derived from `relative_path` so this can't silently drift
    from the actual pinned inventory. Non-icon assets (leaflet.js, chart.js, ...) are skipped --
    nothing reads their content in tests, only whether the file exists, and nothing here writes
    those anyway.

    **Not autouse** -- deliberately opt-in via `pytestmark = pytest.mark.usefixtures("_vendor_icons")`
    in whichever test files actually build a Flask app and render icon-bearing templates. An
    earlier version of this fixture was autouse across the whole suite and broke every
    unrelated test that asserts nothing extra landed in its own `tmp_path` (e.g.
    `test_oscillogram.py`'s/`test_preview.py`'s/`test_spectrogram.py`'s/`test_opus_pipeline.py`'s
    "writes atomically, no leftover temp file" tests) -- caught the same day."""
    vendor_dir = tmp_path / "static" / "vendor"
    for asset in ASSETS:
        if not asset.relative_path.startswith("icons/"):
            continue
        style, name = (
            asset.relative_path.removeprefix("icons/").removesuffix(".svg").split("/")
        )
        dest = vendor_dir / "icons" / style / f"{name}.svg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
            f'viewBox="0 0 24 24" class="icon icon-tabler icons-tabler-{style} '
            f'icon-tabler-{name}"><title>{name}</title></svg>',
        )


@pytest.fixture(autouse=True)
def _isolate_fledermap_config_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`Config.from_env` (and `resolve_static_root`) now read an optional
    config file at a `platformdirs` default location. Redirect that default
    into an empty, per-test tmp_path directory (rather than setting
    FLEDERMAP_CONFIG_FILE to a nonexistent path -- that names an *explicit*
    file, which `_load_config_file` treats as a real misconfiguration when
    absent) so a real `~/.config/fledermap/config.toml` left on the machine
    running the suite -- plausible once this feature ships and someone
    actually uses it -- can never leak into a test's expectations. Tests
    exercising the file-reading path set FLEDERMAP_CONFIG_FILE explicitly,
    which takes priority over this default."""
    monkeypatch.delenv(ENV_CONFIG_FILE, raising=False)
    monkeypatch.setattr(
        platformdirs,
        "user_config_dir",
        lambda *_args, **_kwargs: str(tmp_path / "no-such-config-dir"),
    )


@pytest.fixture(scope="session")
def postgis_url() -> Iterator[str]:
    """A throwaway PostGIS instance. Mirrors poiidx's testing approach."""
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        yield container.get_connection_url()


@pytest.fixture
def engine(postgis_url: str) -> Iterator[Engine]:
    """A per-test-clean database inside the shared session-scoped container.

    The Procrastinate reset below is NOT covered by `drop_all`/`create_all`:
    those only know this project's own ORM tables, while Procrastinate's
    schema is applied by `jobs.app.ensure_schema`, which is idempotent by
    design and so applies once per session and never again. Without an
    explicit reset, deferred jobs outlive the test that made them -- a test
    that never runs a worker leaves its rows `todo` forever, and the next
    test deferring the same `queueing_lock` dies with `AlreadyEnqueued`.
    Lock keys are derived from `audio_hash`, and independent test modules
    reach for the same short literal hashes, so this fires purely on test
    ORDER (pytest-randomly picks it) with nothing wrong in the code under
    test. The existence guard is needed because most tests never touch the
    queue, so `ensure_schema` may not have run in this container yet.
    """
    eng = make_engine(postgis_url)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        queue_present = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
        if queue_present:
            conn.execute(text("DELETE FROM procrastinate_jobs"))
    Base.metadata.drop_all(eng)
    db.create_all(eng)
    yield eng
    eng.dispose()
