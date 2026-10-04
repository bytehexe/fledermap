from __future__ import annotations

from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fledermap.config import (
    ENV_ARCHIVE_ROOTS,
    ENV_BATDETECT2_ANNOTATION_DISPLAY_THRESHOLD,
    ENV_BATDETECT2_ENABLED,
    ENV_BATDETECT2_THRESHOLD,
    ENV_BATTYBIRDNET_ANNOTATION_DISPLAY_THRESHOLD,
    ENV_BATTYBIRDNET_ENABLED,
    ENV_BATTYBIRDNET_THRESHOLD,
    ENV_BATTYBIRDNET_URL,
    ENV_CLASSIFY_IMPLICATION_MIN_CONFIDENCE,
    ENV_CLASSIFY_IMPLICATION_MIN_LIFT,
    ENV_CLASSIFY_IMPLICATION_MIN_SUPPORT,
    ENV_COMBINE_EMT_DISPLAY,
    ENV_CONFIG_FILE,
    ENV_DATABASE_URL,
    ENV_DEFAULT_TIMEZONE,
    ENV_HOST,
    ENV_IUCN_RANGE_EDGE_KM,
    ENV_IUCN_RANGES_ENABLED,
    ENV_LATITUDE,
    ENV_LONGITUDE,
    ENV_MEDIA_ROOT,
    ENV_NOISE_SNR_THRESHOLD_DB,
    ENV_POIIDX_DATABASE_URL,
    ENV_PORT,
    ENV_RTC_ENABLED,
    ENV_SESSION_GAP_HOURS,
    ENV_SITE_EPS_M,
    ENV_SITE_MIN_POINTS,
    ENV_SITE_NAMING_RADIUS_M,
    ENV_STATIC_ROOT,
    ENV_TIMESTAMP_SOURCE,
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


def test_default_noise_snr_threshold_is_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_NOISE_SNR_THRESHOLD_DB, raising=False)
    config = Config.from_env()
    assert config.noise_snr_threshold_db == 2.0


def test_noise_snr_threshold_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_NOISE_SNR_THRESHOLD_DB, "3.5")
    config = Config.from_env()
    assert config.noise_snr_threshold_db == 3.5


def test_invalid_noise_snr_threshold_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_NOISE_SNR_THRESHOLD_DB, "not-a-number")
    with pytest.raises(ConfigError, match="not-a-number"):
        Config.from_env()


def test_batdetect2_defaults_to_disabled_with_threshold_point_eight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_BATDETECT2_ENABLED, raising=False)
    monkeypatch.delenv(ENV_BATDETECT2_THRESHOLD, raising=False)
    config = Config.from_env()
    assert config.batdetect2_enabled is False
    assert config.batdetect2_threshold == 0.8


def test_batdetect2_enabled_and_threshold_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATDETECT2_ENABLED, "true")
    monkeypatch.setenv(ENV_BATDETECT2_THRESHOLD, "0.65")
    config = Config.from_env()
    assert config.batdetect2_enabled is True
    assert config.batdetect2_threshold == 0.65


def test_invalid_batdetect2_enabled_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATDETECT2_ENABLED, "maybe")
    with pytest.raises(ConfigError, match="not a boolean"):
        Config.from_env()


def test_batdetect2_threshold_out_of_range_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATDETECT2_THRESHOLD, "1.5")
    with pytest.raises(ConfigError, match="between 0 and 1"):
        Config.from_env()


def test_batdetect2_annotation_display_threshold_defaults_to_point_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_BATDETECT2_ANNOTATION_DISPLAY_THRESHOLD, raising=False)
    config = Config.from_env()
    assert config.batdetect2_annotation_display_threshold == 0.20


def test_batdetect2_annotation_display_threshold_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATDETECT2_ANNOTATION_DISPLAY_THRESHOLD, "0.35")
    config = Config.from_env()
    assert config.batdetect2_annotation_display_threshold == 0.35


def test_batdetect2_annotation_display_threshold_out_of_range_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATDETECT2_ANNOTATION_DISPLAY_THRESHOLD, "-0.1")
    with pytest.raises(ConfigError, match="between 0 and 1"):
        Config.from_env()


def test_battybirdnet_enabled_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_BATTYBIRDNET_ENABLED, raising=False)
    monkeypatch.delenv(ENV_BATTYBIRDNET_URL, raising=False)
    monkeypatch.delenv(ENV_BATTYBIRDNET_THRESHOLD, raising=False)
    config = Config.from_env()
    assert config.battybirdnet_enabled is False
    assert config.battybirdnet_url is None
    assert config.battybirdnet_threshold == 0.6


def test_battybirdnet_enabled_requires_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATTYBIRDNET_ENABLED, "true")
    monkeypatch.delenv(ENV_BATTYBIRDNET_URL, raising=False)
    with pytest.raises(ConfigError, match="battybirdnet_url"):
        Config.from_env()


def test_battybirdnet_url_and_threshold_are_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATTYBIRDNET_ENABLED, "true")
    monkeypatch.setenv(ENV_BATTYBIRDNET_URL, "http://localhost:7667")
    monkeypatch.setenv(ENV_BATTYBIRDNET_THRESHOLD, "0.75")
    config = Config.from_env()
    assert config.battybirdnet_enabled is True
    assert config.battybirdnet_url == "http://localhost:7667"
    assert config.battybirdnet_threshold == 0.75


def test_battybirdnet_annotation_display_threshold_defaults_to_point_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_BATTYBIRDNET_ANNOTATION_DISPLAY_THRESHOLD, raising=False)
    config = Config.from_env()
    assert config.battybirdnet_annotation_display_threshold == 0.20


def test_battybirdnet_annotation_display_threshold_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATTYBIRDNET_ANNOTATION_DISPLAY_THRESHOLD, "0.4")
    config = Config.from_env()
    assert config.battybirdnet_annotation_display_threshold == 0.4


def test_battybirdnet_annotation_display_threshold_out_of_range_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATTYBIRDNET_ANNOTATION_DISPLAY_THRESHOLD, "1.1")
    with pytest.raises(ConfigError, match="between 0 and 1"):
        Config.from_env()


@pytest.mark.parametrize("value", ["1", "yes"])
def test_battybirdnet_enabled_accepts_the_same_truthy_spellings_as_batdetect2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_BATTYBIRDNET_ENABLED, value)
    monkeypatch.setenv(ENV_BATTYBIRDNET_URL, "http://localhost:7667")
    config = Config.from_env()
    assert config.battybirdnet_enabled is True


def test_combine_emt_display_defaults_to_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_COMBINE_EMT_DISPLAY, raising=False)
    config = Config.from_env()
    assert config.combine_emt_display is True


def test_combine_emt_display_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_COMBINE_EMT_DISPLAY, "false")
    config = Config.from_env()
    assert config.combine_emt_display is False


def test_invalid_combine_emt_display_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_COMBINE_EMT_DISPLAY, "maybe")
    with pytest.raises(ConfigError, match="not a boolean"):
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


def test_default_poiidx_database_url_is_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_POIIDX_DATABASE_URL, raising=False)
    config = Config.from_env()
    assert config.poiidx_database_url is None


def test_poiidx_database_url_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(
        ENV_POIIDX_DATABASE_URL,
        "postgresql://poiidx_user:pw@localhost:5432/poiidx_bats_db",
    )
    config = Config.from_env()
    assert (
        config.poiidx_database_url
        == "postgresql://poiidx_user:pw@localhost:5432/poiidx_bats_db"
    )


def test_empty_poiidx_database_url_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_POIIDX_DATABASE_URL, "")
    with pytest.raises(ConfigError, match=ENV_POIIDX_DATABASE_URL):
        Config.from_env()


def test_default_site_naming_radius(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_SITE_NAMING_RADIUS_M, raising=False)
    config = Config.from_env()
    assert config.site_naming_radius_m == 1000.0


def test_site_naming_radius_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "500")
    config = Config.from_env()
    assert config.site_naming_radius_m == 500.0


def test_zero_site_naming_radius_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "0")
    with pytest.raises(ConfigError, match=ENV_SITE_NAMING_RADIUS_M):
        Config.from_env()


def test_negative_site_naming_radius_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "-1")
    with pytest.raises(ConfigError, match=ENV_SITE_NAMING_RADIUS_M):
        Config.from_env()


def test_invalid_site_naming_radius_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "not-a-number")
    with pytest.raises(ConfigError, match="not-a-number"):
        Config.from_env()


def test_classify_implication_min_support_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_CLASSIFY_IMPLICATION_MIN_SUPPORT, "5")
    config = Config.from_env()
    assert config.classify_implication_min_support == 5


def test_classify_implication_min_confidence_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_CLASSIFY_IMPLICATION_MIN_CONFIDENCE, "0.75")
    config = Config.from_env()
    assert config.classify_implication_min_confidence == 0.75


def test_classify_implication_min_lift_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_CLASSIFY_IMPLICATION_MIN_LIFT, "2.0")
    config = Config.from_env()
    assert config.classify_implication_min_lift == 2.0


def test_rtc_enabled_defaults_to_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_RTC_ENABLED, raising=False)
    config = Config.from_env()
    assert config.rtc_enabled is True


def test_rtc_enabled_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_RTC_ENABLED, "false")
    config = Config.from_env()
    assert config.rtc_enabled is False


def test_latitude_and_longitude_default_to_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_LATITUDE, raising=False)
    monkeypatch.delenv(ENV_LONGITUDE, raising=False)
    config = Config.from_env()
    assert config.latitude is None
    assert config.longitude is None


def test_latitude_and_longitude_are_configurable_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_LATITUDE, "51.5074")
    monkeypatch.setenv(ENV_LONGITUDE, "-0.1278")
    config = Config.from_env()
    assert config.latitude == 51.5074
    assert config.longitude == -0.1278


@pytest.mark.parametrize(
    "set_lat,set_lon",
    [(True, False), (False, True)],
)
def test_latitude_and_longitude_are_both_or_neither(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    set_lat: bool,
    set_lon: bool,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    if set_lat:
        monkeypatch.setenv(ENV_LATITUDE, "51.5074")
    else:
        monkeypatch.delenv(ENV_LATITUDE, raising=False)
    if set_lon:
        monkeypatch.setenv(ENV_LONGITUDE, "-0.1278")
    else:
        monkeypatch.delenv(ENV_LONGITUDE, raising=False)
    with pytest.raises(ConfigError, match="latitude.*longitude|longitude.*latitude"):
        Config.from_env()


@pytest.mark.parametrize(
    "lat_str",
    ["91.0", "-91.0", "999.5"],
)
def test_latitude_out_of_range_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lat_str: str,
) -> None:
    """Latitude must be between -90 and 90 degrees."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_LATITUDE, lat_str)
    monkeypatch.setenv(ENV_LONGITUDE, "0.0")
    with pytest.raises(ConfigError, match="between -90 and 90"):
        Config.from_env()


@pytest.mark.parametrize(
    "lon_str",
    ["181.0", "-181.0", "999.5"],
)
def test_longitude_out_of_range_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lon_str: str,
) -> None:
    """Longitude must be between -180 and 180 degrees."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_LATITUDE, "0.0")
    monkeypatch.setenv(ENV_LONGITUDE, lon_str)
    with pytest.raises(ConfigError, match="between -180 and 180"):
        Config.from_env()


@pytest.mark.parametrize(
    "lat_str",
    ["not_a_number", "51.5074n", "51,5074"],
)
def test_latitude_non_numeric_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lat_str: str,
) -> None:
    """Latitude must be a valid decimal number."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_LATITUDE, lat_str)
    monkeypatch.setenv(ENV_LONGITUDE, "0.0")
    with pytest.raises(ConfigError, match="must be numbers"):
        Config.from_env()


@pytest.mark.parametrize(
    "rtc_str",
    ["maybe", "off"],
)
def test_rtc_enabled_invalid_value_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rtc_str: str,
) -> None:
    """RTC_ENABLED must be 'true' or 'false'."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_RTC_ENABLED, rtc_str)
    with pytest.raises(ConfigError, match="boolean"):
        Config.from_env()


def test_latitude_longitude_from_toml_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Latitude and longitude can be configured via TOML config file."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "database_url = 'postgresql://x/y'\n"
        "archive_roots = ['/tmp/archive']\n"
        "latitude = 60.17\n"
        "longitude = 24.94\n"
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(config_file))
    monkeypatch.delenv(ENV_LATITUDE, raising=False)
    monkeypatch.delenv(ENV_LONGITUDE, raising=False)

    config = Config.from_env()
    assert config.latitude == 60.17
    assert config.longitude == 24.94


def _iucn_base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_IUCN_RANGES_ENABLED, raising=False)
    monkeypatch.delenv(ENV_IUCN_RANGE_EDGE_KM, raising=False)


def test_iucn_ranges_default_to_disabled_with_a_100_km_edge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _iucn_base_env(monkeypatch, tmp_path)
    config = Config.from_env()
    assert config.iucn_ranges_enabled is False
    assert config.iucn_range_edge_km == 100.0


def test_iucn_ranges_settings_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _iucn_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_IUCN_RANGES_ENABLED, "true")
    monkeypatch.setenv(ENV_IUCN_RANGE_EDGE_KM, "250.5")
    config = Config.from_env()
    assert config.iucn_ranges_enabled is True
    assert config.iucn_range_edge_km == 250.5


def test_invalid_iucn_ranges_enabled_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _iucn_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_IUCN_RANGES_ENABLED, "maybe")
    with pytest.raises(ConfigError, match="not a boolean"):
        Config.from_env()


@pytest.mark.parametrize("value", ["0", "-5", "abc", "inf", "nan"])
def test_invalid_iucn_range_edge_km_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    _iucn_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_IUCN_RANGE_EDGE_KM, value)
    with pytest.raises(ConfigError, match="FLEDERMAP_IUCN_RANGE_EDGE_KM"):
        Config.from_env()
