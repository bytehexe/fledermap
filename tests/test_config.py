from __future__ import annotations

from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fledermap.config import (
    ENV_DATABASE_URL,
    ENV_DEFAULT_TIMEZONE,
    ENV_TIMESTAMP_SOURCE,
    Config,
    ConfigError,
)
from fledermap.domain.codes import TimestampSource


def test_missing_database_url_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(ENV_DATABASE_URL, raising=False)
    with pytest.raises(ConfigError, match=ENV_DATABASE_URL):
        Config.from_env(tmp_path)


def test_default_timestamp_source_is_filename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.delenv(ENV_TIMESTAMP_SOURCE, raising=False)
    config = Config.from_env(tmp_path)
    assert config.timestamp_source == TimestampSource.FILENAME


def test_metadata_timestamp_source_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_TIMESTAMP_SOURCE, TimestampSource.METADATA)
    config = Config.from_env(tmp_path)
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
    monkeypatch.setenv(ENV_TIMESTAMP_SOURCE, "filenmae")
    with pytest.raises(ConfigError, match="filenmae") as exc_info:
        Config.from_env(tmp_path)
    message = str(exc_info.value)
    assert TimestampSource.FILENAME in message
    assert TimestampSource.METADATA in message


def test_default_timezone_defaults_to_utc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.delenv(ENV_DEFAULT_TIMEZONE, raising=False)
    config = Config.from_env(tmp_path)
    assert config.default_timezone == UTC


def test_valid_iana_zone_name_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_DEFAULT_TIMEZONE, "Europe/Berlin")
    config = Config.from_env(tmp_path)
    assert config.default_timezone == ZoneInfo("Europe/Berlin")


def test_invalid_timezone_name_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Spec section 11: default_timezone must be configurable. An invalid IANA
    name (a typo) must fail loudly, naming the bad value, rather than silently
    falling back to UTC (Priority 2)."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_DEFAULT_TIMEZONE, "Not/AZone")
    with pytest.raises(ConfigError, match="Not/AZone"):
        Config.from_env(tmp_path)


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
    monkeypatch.setenv(ENV_DEFAULT_TIMEZONE, "")
    with pytest.raises(ConfigError):
        Config.from_env(tmp_path)
