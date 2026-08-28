"""Runtime configuration.

`timestamp_source` defaults to "filename". This is a PROVISIONAL default, not a
settled decision (spec D17): the only evidence available is synthetic and
disagrees with itself by twelve hours. Revisit once real field recordings exist.
Changing it re-derives `recorded_at`; it does not require re-ingesting.

Every setting below can also be set in an optional TOML config file (see
`resolve_config_file`) -- env var wins when both are set, matching the plan
for an eventual Docker deployment (env-only there) without forcing a
standalone/local user to export a shellful of `FLEDERMAP_*` variables just to
run the CLI. See docs/setup.md for the full config file reference.
"""

from __future__ import annotations

import os
import tomllib
import zoneinfo
from dataclasses import dataclass, field
from datetime import UTC, tzinfo
from pathlib import Path
from typing import Any

import platformdirs

from fledermap.domain.codes import TimestampSource

ENV_DATABASE_URL = "FLEDERMAP_DATABASE_URL"
ENV_ARCHIVE_ROOTS = "FLEDERMAP_ARCHIVE_ROOTS"
ENV_TIMESTAMP_SOURCE = "FLEDERMAP_TIMESTAMP_SOURCE"
ENV_DEFAULT_TIMEZONE = "FLEDERMAP_DEFAULT_TIMEZONE"
ENV_SESSION_GAP_HOURS = "FLEDERMAP_SESSION_GAP_HOURS"
ENV_SITE_EPS_M = "FLEDERMAP_SITE_EPS_M"
ENV_SITE_MIN_POINTS = "FLEDERMAP_SITE_MIN_POINTS"
ENV_TRANSECT_DISTANCE_M = "FLEDERMAP_TRANSECT_DISTANCE_M"
ENV_POIIDX_DATABASE_URL = "FLEDERMAP_POIIDX_DATABASE_URL"
ENV_SITE_NAMING_RADIUS_M = "FLEDERMAP_SITE_NAMING_RADIUS_M"
ENV_MEDIA_ROOT = "FLEDERMAP_MEDIA_ROOT"
ENV_STATIC_ROOT = "FLEDERMAP_STATIC_ROOT"
ENV_CONFIG_FILE = "FLEDERMAP_CONFIG_FILE"
ENV_PORT = "FLEDERMAP_PORT"
ENV_HOST = "FLEDERMAP_HOST"

# Every key the config file is allowed to set -- one entry per `Config` field
# that has a `FLEDERMAP_*` env var above. Checked in `_load_config_file` so a
# typo'd key (`sesion_gap_hours`) fails loudly instead of being silently
# ignored forever.
_KNOWN_FILE_KEYS = frozenset(
    {
        "database_url",
        "archive_roots",
        "timestamp_source",
        "default_timezone",
        "session_gap_hours",
        "site_eps_m",
        "site_min_points",
        "transect_distance_m",
        "poiidx_database_url",
        "site_naming_radius_m",
        "media_root",
        "static_root",
        "port",
        "host",
    },
)


class ConfigError(Exception):
    """Required configuration is absent or invalid."""


def resolve_config_file() -> Path:
    """Where the optional TOML config file lives. `FLEDERMAP_CONFIG_FILE`
    names an exact file if set; otherwise a `platformdirs` config directory,
    mirroring `resolve_static_root`'s cache-directory default below.

    `expanduser()` before `resolve()`: without it, a `~` in the path is a
    literal directory named `~`, not the home directory -- `Path.resolve()`
    only normalises an already-resolved path, it doesn't expand tildes."""
    raw = os.environ.get(ENV_CONFIG_FILE)
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(platformdirs.user_config_dir("fledermap")).resolve() / "config.toml"


def _load_config_file() -> tuple[dict[str, Any], Path]:
    """Parse the config file if one exists, returning its contents (raw TOML
    values, keyed by field name) alongside the path used -- every caller that
    reports a bad *value* names this path, not just the env var, so a mistake
    in the file is as easy to locate as a mistake in the environment.

    Absence is only an error when `FLEDERMAP_CONFIG_FILE` named an exact file:
    that's an explicit request for a specific file, unlike the platformdirs
    default location, which is optional exactly like `static_root` itself
    (small, regenerable, not an operator's deliberate data placement)."""
    path = resolve_config_file()
    explicitly_named = bool(os.environ.get(ENV_CONFIG_FILE))
    if not path.exists():
        if explicitly_named:
            msg = f"{ENV_CONFIG_FILE}={path} does not exist."
            raise ConfigError(msg)
        return {}, path

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        msg = f"{path} is not valid TOML: {exc}"
        raise ConfigError(msg) from exc

    unknown = set(data) - _KNOWN_FILE_KEYS
    if unknown:
        msg = (
            f"{path} has unrecognised key(s): {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(sorted(_KNOWN_FILE_KEYS))}."
        )
        raise ConfigError(msg)

    return data, path


def _lookup(env_name: str, file_key: str, file_values: dict[str, Any]) -> Any | None:
    """env > file > None (caller applies its own hardcoded default). Presence
    in `os.environ` -- not truthiness -- decides whether the env var counts as
    "set": an explicitly empty env var (a docker-compose default, an unset
    `.env` line) must still reach the field's own validation and fail there,
    exactly as it did before this file layer existed, rather than silently
    falling through to the file."""
    if env_name in os.environ:
        return os.environ[env_name]
    if file_key in file_values:
        return file_values[file_key]
    return None


def _source_label(env_name: str, file_key: str, config_path: Path) -> str:
    """Which of env/file a bad value came from, for error messages. Recomputed
    at error time from `os.environ` alone -- cheaper than threading it out of
    `_lookup`, and just as correct, since nothing mutates the environment
    in between."""
    if env_name in os.environ:
        return env_name
    return f"{file_key!r} in {config_path}"


def _as_str(value: Any, label: str) -> str:
    """Every string-typed field's raw value, guarded against a config file
    that supplied the wrong TOML type (e.g. a bare number where a quoted
    timezone name belongs). Without this, a non-string reaches `ZoneInfo()`
    or `Path()` and raises a bare `TypeError` instead of `ConfigError`."""
    if isinstance(value, str):
        return value
    msg = f"{label}={value!r} must be a string."
    raise ConfigError(msg)


def _parse_archive_roots(value: Any, label: str) -> tuple[Path, ...]:
    """Env var: comma-separated. Config file: a native TOML array, OR a
    single comma-separated string (accepted the same way, for consistency
    with the env var format rather than forcing a different shape per
    source). Order is preserved -- it's meaningful (design spec §2): when the
    *same* recording (identical audio content) is found under two different
    roots, it's attributed to whichever root was scanned first. A
    same-relative-path collision with *different* content across two roots
    is not a tie at all -- it produces two separate recordings.
    """
    if isinstance(value, str):
        raw_parts = value.split(",")
    elif isinstance(value, list):
        raw_parts = [_as_str(v, label) for v in value]
    else:
        msg = (
            f"{label}={value!r} must be a comma-separated string or an array of paths."
        )
        raise ConfigError(msg)
    parts = [p.strip() for p in raw_parts if p.strip()]
    if not parts:
        msg = f"{label} must name at least one directory."
        raise ConfigError(msg)
    return tuple(Path(p).expanduser().resolve() for p in parts)


def resolve_media_root() -> Path:
    """Where derived media (spectrograms, previews) lives.

    Optional as of 2026-08-26, reversing the original Phase 3 decision (see
    the dated deviation note in `docs/superpowers/specs/
    2026-08-25-fledermap-phase3-media-jobs-design.md` §10) that `media_root`
    stay required with no default, matching `database_url`/`archive_roots`.
    Falls back to a `platformdirs` *data* directory, not a *cache* directory
    like `resolve_static_root` below -- derived media is, in principle,
    re-derivable from the archive, but expensive to regenerate at scale, so
    it doesn't fit the "small, disposable, freely-clearable" shape a cache
    directory implies.

    Consults the config file too (not just the env var), and shares
    `_load_config_file` with `Config.from_env`, mirroring `resolve_static_root`.

    `expanduser()` before `resolve()`, same as `resolve_config_file` --
    otherwise `~` is a literal directory name, not the home directory."""
    raw = os.environ.get(ENV_MEDIA_ROOT)
    if raw:
        return Path(raw).expanduser().resolve()
    file_values, config_path = _load_config_file()
    file_raw = file_values.get("media_root")
    if file_raw:
        path = _as_str(file_raw, f"'media_root' in {config_path}")
        return Path(path).expanduser().resolve()
    return Path(platformdirs.user_data_dir("fledermap")).resolve()


def resolve_static_root() -> Path:
    """Where fetched vendor JS/CSS assets (Leaflet, HTMX, Alpine -- see
    `services/vendor_assets.py`) live. Unlike `resolve_media_root` above,
    these are small, regenerable, non-precious cache-like files -- refetched
    from unpkg.com and hash-verified on demand -- not an operator's
    deliberate data-placement decision, so a `platformdirs` *cache*-dir
    default (rather than the *data*-dir default `media_root` uses) is the
    right fit here (design spec P4-5). A standalone function, not a `Config`
    method, so callers like `fledermap fetch-assets` can call it without
    building a full `Config` (which requires `archive_roots`, irrelevant to
    fetching static assets).

    Consults the config file too (not just the env var), and shares
    `_load_config_file` with `Config.from_env` rather than re-deriving its
    own notion of "configured static root" -- the two must never disagree,
    which `test_resolve_static_root_matches_config` pins down.

    `expanduser()` before `resolve()`, same as `resolve_config_file` --
    otherwise `~` is a literal directory name, not the home directory."""
    raw = os.environ.get(ENV_STATIC_ROOT)
    if raw:
        return Path(raw).expanduser().resolve()
    file_values, config_path = _load_config_file()
    file_raw = file_values.get("static_root")
    if file_raw:
        path = _as_str(file_raw, f"'static_root' in {config_path}")
        return Path(path).expanduser().resolve()
    return Path(platformdirs.user_cache_dir("fledermap")).resolve()


@dataclass(frozen=True)
class Config:
    database_url: str
    archive_roots: tuple[Path, ...]
    timestamp_source: TimestampSource = TimestampSource.FILENAME
    # The offset used only when NO source in a file carries any offset
    # evidence at all (spec section 11). An IANA zone name, not a fixed UTC
    # offset: a named zone handles DST correctly and a fixed offset doesn't —
    # relevant precisely because the project's own spike found a DST-adjacent
    # offset discrepancy. Defaults to UTC, matching spec section 11's own
    # stated default.
    default_timezone: tzinfo = UTC
    # Spec section 7: "tuning is free" — eps, minpoints and session_gap are config,
    # and site rebuilding is idempotent.
    session_gap_hours: float = 6.0
    site_eps_m: float = 75.0
    site_min_points: int = 3
    # Design spec 2026-08-27-fledermap-phase5b-sessions-design.md section 6:
    # the GPS-spread threshold `derive/sessions.py`'s `classify_kind` uses to
    # suggest TRANSECT over STATIONARY. Real derivation logic (unlike a UI
    # hint), so it gets the same operational-tuning treatment as
    # `site_eps_m`/`session_gap_hours` rather than a code constant.
    transect_distance_m: float = 150.0
    # Optional (design spec 2026-08-28-fledermap-poiidx-site-naming-design.md,
    # decision SN-2): unset means the site-naming integration is off entirely
    # -- sites keep today's coordinate-fallback label, nothing errors, nothing
    # blocks. When set, this must point at a dedicated poiidx_bats_db, never
    # poiidx_db (the owner's real index) or bats_db (Fledermap's own storage)
    # -- see services/site_naming.py's connection-safety comment.
    poiidx_database_url: str | None = None
    # How far (metres) to search for a nearby named POI before falling back
    # to the administrative hierarchy string. Picked by analogy to
    # site_eps_m/transect_distance_m's defaults, not from parent-spec
    # guidance -- this task owns the default the same way P2-5 owned
    # site_min_points's.
    site_naming_radius_m: float = 300.0
    # Optional since 2026-08-26 (default_factory, not a plain default, for the
    # same reason as static_root below -- it must actually run at
    # construction time and pick up whatever's configured then). It must
    # still be distinct from archive_roots -- writing into the archive would
    # violate D16's read-only invariant on the source tree. See
    # resolve_media_root's docstring for the platformdirs rationale and the
    # pointer to the dated deviation note reversing the original
    # required-with-no-default decision.
    media_root: Path = field(default_factory=resolve_media_root)
    # Optional, same as media_root above, but see resolve_static_root's
    # docstring for why it defaults to a platformdirs *cache* dir rather than
    # the *data* dir media_root uses. default_factory, not a plain default,
    # so it's actually called at construction time and picks
    # up whatever FLEDERMAP_STATIC_ROOT is set to at that moment -- including
    # in tests that monkeypatch the env var before calling `from_env`.
    static_root: Path = field(default_factory=resolve_static_root)
    # `serve`-specific, but lives here rather than as a plain Click default so
    # it goes through the same env-var/config-file layering as everything
    # else. `serve --port`/`--host` still win when given explicitly -- a CLI
    # flag typed at invocation time should always beat a standing default.
    port: int = 5000
    host: str = "127.0.0.1"

    @classmethod
    def from_env(cls) -> Config:
        file_values, config_path = _load_config_file()

        url = _lookup(ENV_DATABASE_URL, "database_url", file_values)
        if not url:
            msg = (
                f"{ENV_DATABASE_URL} is not set, and no 'database_url' entry "
                f"exists in {config_path}. Point one of them at Fledermap's "
                "own database (bats_db) — never at poiidx's, which drops "
                "and recreates its tables on any config change."
            )
            raise ConfigError(msg)
        url = _as_str(url, _source_label(ENV_DATABASE_URL, "database_url", config_path))

        archive_roots_raw = _lookup(ENV_ARCHIVE_ROOTS, "archive_roots", file_values)
        if not archive_roots_raw:
            msg = (
                f"{ENV_ARCHIVE_ROOTS} is not set, and no 'archive_roots' entry "
                f"exists in {config_path}. Point it at one or more directories "
                "holding recordings -- comma-separated for the env var, or a "
                "TOML array in the config file."
            )
            raise ConfigError(msg)
        archive_roots = _parse_archive_roots(
            archive_roots_raw,
            _source_label(ENV_ARCHIVE_ROOTS, "archive_roots", config_path),
        )

        timestamp_source_raw = _lookup(
            ENV_TIMESTAMP_SOURCE,
            "timestamp_source",
            file_values,
        )
        if timestamp_source_raw is None:
            timestamp_source_raw = TimestampSource.FILENAME.value
        try:
            timestamp_source = TimestampSource(timestamp_source_raw)
        except ValueError as exc:
            valid = ", ".join(s.value for s in TimestampSource)
            label = _source_label(ENV_TIMESTAMP_SOURCE, "timestamp_source", config_path)
            msg = (
                f"{label}={timestamp_source_raw!r} is not a valid "
                f"timestamp source. Valid options: {valid}."
            )
            raise ConfigError(msg) from exc

        timezone_name = _lookup(ENV_DEFAULT_TIMEZONE, "default_timezone", file_values)
        if timezone_name is None:
            default_timezone: tzinfo = UTC
        else:
            label = _source_label(ENV_DEFAULT_TIMEZONE, "default_timezone", config_path)
            timezone_name = _as_str(timezone_name, label)
            try:
                default_timezone = zoneinfo.ZoneInfo(timezone_name)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
                msg = (
                    f"{label}={timezone_name!r} is not a "
                    "known IANA timezone name (e.g. 'Europe/Berlin')."
                )
                raise ConfigError(msg) from exc

        session_gap_raw = _lookup(
            ENV_SESSION_GAP_HOURS,
            "session_gap_hours",
            file_values,
        )
        if session_gap_raw is None:
            session_gap_hours = 6.0
        else:
            label = _source_label(
                ENV_SESSION_GAP_HOURS,
                "session_gap_hours",
                config_path,
            )
            # bool is a subclass of int (and float(True) == 1.0), and a
            # config file -- unlike an env var, always a string -- can hand
            # us one directly if someone writes `session_gap_hours = true`.
            if isinstance(session_gap_raw, bool):
                msg = f"{label}={session_gap_raw!r} is not a number of hours."
                raise ConfigError(msg)
            try:
                session_gap_hours = float(session_gap_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={session_gap_raw!r} is not a number of hours."
                raise ConfigError(msg) from exc
            # `not x > 0` rather than `x <= 0`: `float("nan")` parses fine
            # and is <= 0 == False, so a plain `<= 0` would let it through to
            # crash later inside `timedelta`.
            if not session_gap_hours > 0:
                msg = f"{label}={session_gap_raw!r} is not a positive number of hours."
                raise ConfigError(msg)

        site_eps_raw = _lookup(ENV_SITE_EPS_M, "site_eps_m", file_values)
        if site_eps_raw is None:
            site_eps_m = 75.0
        else:
            label = _source_label(ENV_SITE_EPS_M, "site_eps_m", config_path)
            if isinstance(site_eps_raw, bool):  # see session_gap_hours above
                msg = f"{label}={site_eps_raw!r} is not a number of metres."
                raise ConfigError(msg)
            try:
                site_eps_m = float(site_eps_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={site_eps_raw!r} is not a number of metres."
                raise ConfigError(msg) from exc
            if not site_eps_m > 0:  # also rejects nan; see above
                msg = f"{label}={site_eps_raw!r} is not a positive number of metres."
                raise ConfigError(msg)

        site_min_points_raw = _lookup(
            ENV_SITE_MIN_POINTS,
            "site_min_points",
            file_values,
        )
        if site_min_points_raw is None:
            site_min_points = 3
        else:
            label = _source_label(ENV_SITE_MIN_POINTS, "site_min_points", config_path)
            # bool again (see session_gap_hours), plus float: int(3.5) == 3
            # would silently truncate a bad config-file value instead of
            # raising, unlike every env var here, which is always a string
            # and so already fails loudly via the ValueError below.
            if isinstance(site_min_points_raw, bool) or isinstance(
                site_min_points_raw,
                float,
            ):
                msg = f"{label}={site_min_points_raw!r} is not an integer."
                raise ConfigError(msg)
            try:
                site_min_points = int(site_min_points_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={site_min_points_raw!r} is not an integer."
                raise ConfigError(msg) from exc
            if site_min_points < 1:
                msg = f"{label}={site_min_points_raw!r} is not an integer of 1 or more."
                raise ConfigError(msg)

        transect_distance_raw = _lookup(
            ENV_TRANSECT_DISTANCE_M,
            "transect_distance_m",
            file_values,
        )
        if transect_distance_raw is None:
            transect_distance_m = 150.0
        else:
            label = _source_label(
                ENV_TRANSECT_DISTANCE_M,
                "transect_distance_m",
                config_path,
            )
            if isinstance(transect_distance_raw, bool):  # see session_gap_hours above
                msg = f"{label}={transect_distance_raw!r} is not a number of metres."
                raise ConfigError(msg)
            try:
                transect_distance_m = float(transect_distance_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={transect_distance_raw!r} is not a number of metres."
                raise ConfigError(msg) from exc
            if not transect_distance_m > 0:  # also rejects nan; see site_eps_m above
                msg = (
                    f"{label}={transect_distance_raw!r} is not a positive "
                    "number of metres."
                )
                raise ConfigError(msg)

        port_raw = _lookup(ENV_PORT, "port", file_values)
        if port_raw is None:
            port = 5000
        else:
            label = _source_label(ENV_PORT, "port", config_path)
            if isinstance(port_raw, bool) or isinstance(port_raw, float):
                msg = f"{label}={port_raw!r} is not an integer."
                raise ConfigError(msg)
            try:
                port = int(port_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={port_raw!r} is not an integer."
                raise ConfigError(msg) from exc
            if not 1 <= port <= 65535:
                msg = f"{label}={port_raw!r} is not a valid TCP port (1-65535)."
                raise ConfigError(msg)

        host_raw = _lookup(ENV_HOST, "host", file_values)
        if host_raw is None:
            host = "127.0.0.1"
        else:
            host = _as_str(host_raw, _source_label(ENV_HOST, "host", config_path))

        poiidx_database_url_raw = _lookup(
            ENV_POIIDX_DATABASE_URL,
            "poiidx_database_url",
            file_values,
        )
        if poiidx_database_url_raw is None:
            poiidx_database_url = None
        else:
            label = _source_label(
                ENV_POIIDX_DATABASE_URL,
                "poiidx_database_url",
                config_path,
            )
            poiidx_database_url = _as_str(poiidx_database_url_raw, label)
            if not poiidx_database_url:
                msg = f"{label} is set but empty. Unset it entirely to disable site naming."
                raise ConfigError(msg)

        site_naming_radius_raw = _lookup(
            ENV_SITE_NAMING_RADIUS_M,
            "site_naming_radius_m",
            file_values,
        )
        if site_naming_radius_raw is None:
            site_naming_radius_m = 300.0
        else:
            label = _source_label(
                ENV_SITE_NAMING_RADIUS_M,
                "site_naming_radius_m",
                config_path,
            )
            if isinstance(site_naming_radius_raw, bool):  # see session_gap_hours above
                msg = f"{label}={site_naming_radius_raw!r} is not a number of metres."
                raise ConfigError(msg)
            try:
                site_naming_radius_m = float(site_naming_radius_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={site_naming_radius_raw!r} is not a number of metres."
                raise ConfigError(msg) from exc
            if not site_naming_radius_m > 0:  # also rejects nan; see site_eps_m above
                msg = (
                    f"{label}={site_naming_radius_raw!r} is not a positive "
                    "number of metres."
                )
                raise ConfigError(msg)

        return cls(
            database_url=url,
            archive_roots=archive_roots,
            timestamp_source=timestamp_source,
            default_timezone=default_timezone,
            session_gap_hours=session_gap_hours,
            site_eps_m=site_eps_m,
            site_min_points=site_min_points,
            transect_distance_m=transect_distance_m,
            poiidx_database_url=poiidx_database_url,
            site_naming_radius_m=site_naming_radius_m,
            port=port,
            host=host,
        )
