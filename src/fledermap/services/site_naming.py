"""poiidx query and job-enqueue logic for site naming (design spec
2026-08-28-fledermap-poiidx-site-naming-design.md).

WARNING: the connection built here must never point at `poiidx_db` (the
owner's own, separate, pre-existing POI index) or `bats_db` (Fledermap's own
real storage -- see the warning at the top of `store/db.py`). It must point
at `poiidx_bats_db`, a third, dedicated database this integration owns
exclusively. poiidx hashes its own schema and filter config on `init()` and
DROPS AND RECREATES ALL TABLES on any mismatch -- same hazard, different
database.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

import poiidx
import yaml

_FILTER_CONFIG_FILE = "poiidx_filter_config.yaml"


def _poiidx_connection_kwargs(database_url: str) -> dict[str, Any]:
    """Parse FLEDERMAP_POIIDX_DATABASE_URL into poiidx.init()'s discrete
    connect kwargs. poiidx.connect() forwards straight to peewee's
    PostgresqlDatabase.init(), which takes host/port/user/password/database
    separately -- confirmed against poiidx's own README and example.py, not
    a single connection-string argument."""
    parsed = urlsplit(database_url)
    database = parsed.path.lstrip("/")
    if not (parsed.hostname and parsed.username and parsed.password and database):
        msg = (
            "FLEDERMAP_POIIDX_DATABASE_URL must be a "
            "postgresql://user:password@host:port/database URL, got "
            f"{database_url!r}"
        )
        raise ValueError(msg)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "database": database,
    }


def _load_filter_config() -> list[dict[str, Any]]:
    """Fledermap's own toponymy-focused filter config (design spec §2), NOT
    poiidx's shipped default -- must stay byte-identical across every
    poiidx.init() call, or poiidx drops and recreates every table in
    poiidx_bats_db."""
    raw = (
        files("fledermap.services.data")
        .joinpath(_FILTER_CONFIG_FILE)
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(raw)


_connected = False


def ensure_connected(poiidx_database_url: str) -> None:
    """Idempotent within a process. poiidx.init() re-hashes the filter config
    against what's already stored on every call -- harmless to call more
    than once with the SAME config, but there's no reason to pay that
    comparison on every single job when a module-level flag can skip it
    after the first call in this process. Never reset except by tests."""
    global _connected
    if _connected:
        return
    poiidx.init(
        filter_config=_load_filter_config(),
        **_poiidx_connection_kwargs(poiidx_database_url),
    )
    _connected = True
