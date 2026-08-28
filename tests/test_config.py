from __future__ import annotations

from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fledermap.config import (
    ENV_ARCHIVE_ROOTS,
    ENV_CONFIG_FILE,
    ENV_DATABASE_URL,
    ENV_DEFAULT_TIMEZONE,
    ENV_HOST,
    ENV_MEDIA_ROOT,
    ENV_PORT,
    ENV_SESSION_GAP_HOURS,
    ENV_SITE_EPS_M,
    ENV_SITE_MIN_POINTS,
    ENV_STATIC_ROOT,
    ENV_TIMESTAMP_SOURCE,
    ENV_TRANSECT_DISTANCE_M,
    Config,
    ConfigError,
    resolve_media_root,
    resolve_static_root,
)
from fledermap.domain.codes import TimestampSource


def test_missing_database_url_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(ENV_DATABASE_URL, raising=False)
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    with pytest.raises(ConfigError, match=ENV_DATABASE_URL):
        Config.from_env()


def test_missing_archive_roots_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_ARCHIVE_ROOTS, raising=False)
    with pytest.raises(ConfigError, match=ENV_ARCHIVE_ROOTS):
        Config.from_env()


def test_single_archive_root_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    root = tmp_path / "archive"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(root))
    config = Config.from_env()
    assert config.archive_roots == (root.resolve(),)


def test_multiple_archive_roots_are_comma_separated_and_order_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    first = tmp_path / "syncthing"
    second = tmp_path / "sdcard-dump"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, f"{first},{second}")
    config = Config.from_env()
    assert config.archive_roots == (first.resolve(), second.resolve())


def test_archive_roots_strips_whitespace_and_drops_empty_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A trailing comma (a common hand-edited env var mistake) must not
    produce a phantom empty root, and " b" must resolve the same as "b"."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, f"{a}, {b},")
    config = Config.from_env()
    assert config.archive_roots == (a.resolve(), b.resolve())


def test_archive_roots_expands_tilde_per_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, "~/archive")
    config = Config.from_env()
    assert config.archive_roots == ((tmp_path / "archive").resolve(),)


def test_config_file_supplies_archive_roots_as_a_toml_array(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        f'archive_roots = ["{first}", "{second}"]\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.delenv(ENV_ARCHIVE_ROOTS, raising=False)
    config = Config.from_env()
    assert config.archive_roots == (first.resolve(), second.resolve())


def test_env_archive_roots_overrides_config_file_archive_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        f'archive_roots = ["{tmp_path / "from-file"}"]\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    from_env_root = tmp_path / "from-env"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(from_env_root))
    config = Config.from_env()
    assert config.archive_roots == (from_env_root.resolve(),)


def test_non_list_non_string_archive_roots_in_config_file_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        'database_url = "postgresql://x/y"\narchive_roots = 5\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    with pytest.raises(ConfigError, match="archive_roots"):
        Config.from_env()


def test_default_timestamp_source_is_filename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_TIMESTAMP_SOURCE, raising=False)
    config = Config.from_env()
    assert config.timestamp_source == TimestampSource.FILENAME


def test_metadata_timestamp_source_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_TIMESTAMP_SOURCE, TimestampSource.METADATA)
    config = Config.from_env()
    assert config.timestamp_source == TimestampSource.METADATA


def test_invalid_timestamp_source_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A typo here (task-13, defect 4) would otherwise silently and
    permanently change how `recorded_at` is derived for the whole archive,
    with no error — this is the CLI's own user-facing env var, so a bad
    value must fail loudly, naming both the bad value and the valid options."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_TIMESTAMP_SOURCE, "filenmae")
    with pytest.raises(ConfigError, match="filenmae") as exc_info:
        Config.from_env()
    message = str(exc_info.value)
    assert TimestampSource.FILENAME in message
    assert TimestampSource.METADATA in message


def test_default_timezone_defaults_to_utc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_DEFAULT_TIMEZONE, raising=False)
    config = Config.from_env()
    assert config.default_timezone == UTC


def test_valid_iana_zone_name_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_DEFAULT_TIMEZONE, "Europe/Berlin")
    config = Config.from_env()
    assert config.default_timezone == ZoneInfo("Europe/Berlin")


def test_invalid_timezone_name_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Spec section 11: default_timezone must be configurable. An invalid IANA
    name (a typo) must fail loudly, naming the bad value, rather than silently
    falling back to UTC (Priority 2)."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_DEFAULT_TIMEZONE, "Not/AZone")
    with pytest.raises(ConfigError, match="Not/AZone"):
        Config.from_env()


def test_malformed_timezone_key_raises_config_error_not_valueerror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`zoneinfo.ZoneInfo` raises a bare `ValueError`, not `ZoneInfoNotFoundError`,
    for a MALFORMED key (empty, absolute, a `..` component, a trailing slash) —
    as opposed to a well-formed but unknown one. A declared-but-empty env var
    (a docker-compose default, an unset `.env` line) is a plausible real
    misconfiguration and must fail as cleanly as an unknown zone name, not
    escape as an unhandled ValueError (whole-branch fix round 1 re-review)."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_DEFAULT_TIMEZONE, "")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_default_session_gap_is_six_hours(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_SESSION_GAP_HOURS, raising=False)
    config = Config.from_env()
    assert config.session_gap_hours == 6.0


def test_session_gap_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_SESSION_GAP_HOURS, "4.5")
    config = Config.from_env()
    assert config.session_gap_hours == 4.5


def test_invalid_session_gap_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SESSION_GAP_HOURS, "not-a-number")
    with pytest.raises(ConfigError, match="not-a-number"):
        Config.from_env()


def test_default_site_eps_and_min_points(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_SITE_EPS_M, raising=False)
    monkeypatch.delenv(ENV_SITE_MIN_POINTS, raising=False)
    config = Config.from_env()
    assert config.site_eps_m == 75.0
    assert config.site_min_points == 3


def test_site_eps_and_min_points_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_SITE_EPS_M, "50")
    monkeypatch.setenv(ENV_SITE_MIN_POINTS, "5")
    config = Config.from_env()
    assert config.site_eps_m == 50.0
    assert config.site_min_points == 5


def test_invalid_site_min_points_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_MIN_POINTS, "not-an-int")
    with pytest.raises(ConfigError, match="not-an-int"):
        Config.from_env()


def test_zero_session_gap_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Range violations must fail at config parse time like every other bad
    value here (cf. `test_malformed_timezone_key_raises_config_error_not_valueerror`),
    not after migrations have run, deep inside scikit-learn or `timedelta`."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SESSION_GAP_HOURS, "0")
    with pytest.raises(ConfigError, match=ENV_SESSION_GAP_HOURS):
        Config.from_env()


def test_negative_session_gap_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SESSION_GAP_HOURS, "-1")
    with pytest.raises(ConfigError, match=ENV_SESSION_GAP_HOURS):
        Config.from_env()


def test_zero_site_eps_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_EPS_M, "0")
    with pytest.raises(ConfigError, match=ENV_SITE_EPS_M):
        Config.from_env()


def test_negative_site_eps_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_EPS_M, "-5")
    with pytest.raises(ConfigError, match=ENV_SITE_EPS_M):
        Config.from_env()


def test_default_transect_distance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_TRANSECT_DISTANCE_M, raising=False)
    config = Config.from_env()
    assert config.transect_distance_m == 150.0


def test_transect_distance_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "300")
    config = Config.from_env()
    assert config.transect_distance_m == 300.0


def test_zero_transect_distance_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "0")
    with pytest.raises(ConfigError, match=ENV_TRANSECT_DISTANCE_M):
        Config.from_env()


def test_negative_transect_distance_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "-1")
    with pytest.raises(ConfigError, match=ENV_TRANSECT_DISTANCE_M):
        Config.from_env()


def test_invalid_transect_distance_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "not-a-number")
    with pytest.raises(ConfigError, match="not-a-number"):
        Config.from_env()


def test_zero_site_min_points_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_MIN_POINTS, "0")
    with pytest.raises(ConfigError, match=ENV_SITE_MIN_POINTS):
        Config.from_env()


def test_default_port_is_5000(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_PORT, raising=False)
    config = Config.from_env()
    assert config.port == 5000


def test_port_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_PORT, "8080")
    config = Config.from_env()
    assert config.port == 8080


def test_invalid_port_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_PORT, "not-a-port")
    with pytest.raises(ConfigError, match=ENV_PORT):
        Config.from_env()


def test_zero_port_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_PORT, "0")
    with pytest.raises(ConfigError, match=ENV_PORT):
        Config.from_env()


def test_port_above_65535_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_PORT, "65536")
    with pytest.raises(ConfigError, match=ENV_PORT):
        Config.from_env()


def test_float_port_raises_config_error_not_truncates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Same truncation hazard as `site_min_points` -- a config-file float
    must raise, not silently become `int(8080.5) == 8080`."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    path = _write_config_file(tmp_path, "port = 8080.5\n")
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    with pytest.raises(ConfigError, match="port"):
        Config.from_env()


def test_config_file_supplies_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        "port = 8080\n",
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_PORT, raising=False)

    config = Config.from_env()

    assert config.port == 8080


def test_env_port_overrides_config_file_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        "port = 8080\n",
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_PORT, "9090")

    config = Config.from_env()

    assert config.port == 9090


def test_default_host_is_loopback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_HOST, raising=False)
    config = Config.from_env()
    assert config.host == "127.0.0.1"


def test_host_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_HOST, "0.0.0.0")
    config = Config.from_env()
    assert config.host == "0.0.0.0"


def test_config_file_supplies_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        'host = "0.0.0.0"\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_HOST, raising=False)

    config = Config.from_env()

    assert config.host == "0.0.0.0"


def test_non_string_host_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(tmp_path, "host = 5\n")
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    with pytest.raises(ConfigError, match="host"):
        Config.from_env()


def test_nan_session_gap_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`float("nan")` parses, and `nan <= 0` is False — a plain `<= 0` check
    would let it through to raise inside `timedelta(hours=nan)` instead."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SESSION_GAP_HOURS, "nan")
    with pytest.raises(ConfigError, match=ENV_SESSION_GAP_HOURS):
        Config.from_env()


def test_nan_site_eps_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Same `not x > 0` guard, same NaN hazard, as
    `test_nan_session_gap_raises_config_error` — `site_eps_m` reaches
    scikit-learn's DBSCAN `eps` parameter instead of `timedelta`, but a bare
    `<= 0` check would just as surely let `nan` through uncaught."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_EPS_M, "nan")
    with pytest.raises(ConfigError, match=ENV_SITE_EPS_M):
        Config.from_env()


def test_media_root_defaults_via_platformdirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_MEDIA_ROOT, raising=False)

    config = Config.from_env()

    import platformdirs

    assert config.media_root == Path(platformdirs.user_data_dir("fledermap")).resolve()


def test_resolve_media_root_matches_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mirrors test_resolve_static_root_matches_config: resolve_media_root()
    is callable standalone, and must agree with what Config.media_root
    resolves to given the same environment."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    config = Config.from_env()

    assert resolve_media_root() == config.media_root


def test_media_root_is_resolved_to_an_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    relative = "some/media/dir"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_MEDIA_ROOT, relative)

    config = Config.from_env()

    assert config.media_root == (tmp_path / relative).resolve()
    assert config.media_root.is_absolute()


def test_media_root_expands_tilde(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, "~/media")

    config = Config.from_env()

    assert config.media_root == (tmp_path / "media").resolve()


def test_static_root_env_var_expands_tilde(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_STATIC_ROOT, "~/static")

    config = Config.from_env()

    assert config.static_root == (tmp_path / "static").resolve()


def test_static_root_config_file_expands_tilde(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        'static_root = "~/static"\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_STATIC_ROOT, raising=False)

    config = Config.from_env()

    assert config.static_root == (tmp_path / "static").resolve()
    assert resolve_static_root() == config.static_root


def test_static_root_defaults_via_platformdirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_STATIC_ROOT, raising=False)

    config = Config.from_env()

    import platformdirs

    assert (
        config.static_root == Path(platformdirs.user_cache_dir("fledermap")).resolve()
    )


def test_static_root_respects_explicit_env_var(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    explicit = tmp_path / "static"
    monkeypatch.setenv(ENV_STATIC_ROOT, str(explicit))

    config = Config.from_env()

    assert config.static_root == explicit.resolve()


def test_resolve_static_root_matches_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """services/vendor_assets.py calls resolve_static_root() directly,
    without building a full Config -- this pins that it agrees with what
    Config.static_root resolves to, given the same environment."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_STATIC_ROOT, str(tmp_path / "static"))

    config = Config.from_env()

    assert resolve_static_root() == config.static_root


# --- config file ------------------------------------------------------------
#
# `conftest.py`'s autouse `_isolate_fledermap_config_file` fixture already
# points FLEDERMAP_CONFIG_FILE at a nonexistent path for every test above --
# these tests override it to point at a real file they write themselves.


def _write_config_file(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(contents)
    return path


def test_config_file_supplies_database_url_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://from-file/y"\n'
        f'media_root = "{tmp_path / "media"}"\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.delenv(ENV_DATABASE_URL, raising=False)
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))

    config = Config.from_env()

    assert config.database_url == "postgresql://from-file/y"


def test_env_var_overrides_config_file_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://from-file/y"\n'
        f'media_root = "{tmp_path / "media"}"\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://from-env/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))

    config = Config.from_env()

    assert config.database_url == "postgresql://from-env/y"


def test_missing_config_file_at_default_location_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No FLEDERMAP_CONFIG_FILE at all -- the conftest autouse fixture already
    redirects the *default* platformdirs location into an empty directory,
    so this exercises exactly that "nothing configured" path and pins that
    it is not an error (unlike naming a file explicitly and having it be
    absent -- see test_explicitly_named_missing_config_file_raises)."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    config = Config.from_env()

    assert config.database_url == "postgresql://x/y"


def test_explicitly_named_missing_config_file_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Distinct from the default-location case above: naming
    FLEDERMAP_CONFIG_FILE explicitly is a request for that exact file, so its
    absence is a real misconfiguration, not "no file configured"."""
    missing = tmp_path / "does-not-exist.toml"
    monkeypatch.setenv(ENV_CONFIG_FILE, str(missing))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))

    with pytest.raises(ConfigError, match=ENV_CONFIG_FILE):
        Config.from_env()


def test_malformed_toml_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(tmp_path, "database_url = not valid toml [[[\n")
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))

    with pytest.raises(ConfigError, match="not valid TOML"):
        Config.from_env()


def test_config_file_unknown_key_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A typo'd key (`sesion_gap_hours`) must fail loudly rather than being
    silently ignored forever."""
    path = _write_config_file(tmp_path, 'sesion_gap_hours = "4"\n')
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    with pytest.raises(ConfigError, match="sesion_gap_hours"):
        Config.from_env()


def test_config_file_supplies_numeric_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        "session_gap_hours = 4.5\nsite_eps_m = 50\nsite_min_points = 5\n",
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    config = Config.from_env()

    assert config.session_gap_hours == 4.5
    assert config.site_eps_m == 50.0
    assert config.site_min_points == 5


def test_config_file_float_site_min_points_raises_not_truncates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`int(3.5) == 3` would silently truncate a bad value -- this must raise
    instead, the same as `test_invalid_site_min_points_raises_config_error`
    does for the env-var path."""
    path = _write_config_file(tmp_path, "site_min_points = 3.5\n")
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    with pytest.raises(ConfigError, match="site_min_points"):
        Config.from_env()


def test_config_file_bool_session_gap_hours_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(tmp_path, "session_gap_hours = true\n")
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    with pytest.raises(ConfigError, match="session_gap_hours"):
        Config.from_env()


def test_config_file_non_string_default_timezone_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(tmp_path, "default_timezone = 5\n")
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    with pytest.raises(ConfigError, match="default_timezone"):
        Config.from_env()


def test_config_file_supplies_media_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media = tmp_path / "media"
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\nmedia_root = "{media}"\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_MEDIA_ROOT, raising=False)

    config = Config.from_env()

    assert config.media_root == media.resolve()


def test_config_file_supplies_static_root_and_resolve_static_root_agrees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    static = tmp_path / "static"
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        f'static_root = "{static}"\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_STATIC_ROOT, raising=False)

    config = Config.from_env()

    assert config.static_root == static.resolve()
    assert resolve_static_root() == config.static_root
