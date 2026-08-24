from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.config import (
    ENV_DATABASE_URL,
    ENV_TIMESTAMP_SOURCE,
    Config,
    ConfigError,
)
from fledermap.ingest.merge import TIMESTAMP_SOURCE_FILENAME, TIMESTAMP_SOURCE_METADATA


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
    assert config.timestamp_source == TIMESTAMP_SOURCE_FILENAME


def test_metadata_timestamp_source_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_TIMESTAMP_SOURCE, TIMESTAMP_SOURCE_METADATA)
    config = Config.from_env(tmp_path)
    assert config.timestamp_source == TIMESTAMP_SOURCE_METADATA


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
    assert TIMESTAMP_SOURCE_FILENAME in message
    assert TIMESTAMP_SOURCE_METADATA in message
