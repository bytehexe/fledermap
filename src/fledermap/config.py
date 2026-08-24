"""Runtime configuration.

`timestamp_source` defaults to "filename". This is a PROVISIONAL default, not a
settled decision (spec D17): the only evidence available is synthetic and
disagrees with itself by twelve hours. Revisit once real field recordings exist.
Changing it re-derives `recorded_at`; it does not require re-ingesting.
"""

from __future__ import annotations

import os
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, tzinfo
from pathlib import Path

from fledermap.domain.codes import TimestampSource

ENV_DATABASE_URL = "FLEDERMAP_DATABASE_URL"
ENV_TIMESTAMP_SOURCE = "FLEDERMAP_TIMESTAMP_SOURCE"
ENV_DEFAULT_TIMEZONE = "FLEDERMAP_DEFAULT_TIMEZONE"
ENV_SESSION_GAP_HOURS = "FLEDERMAP_SESSION_GAP_HOURS"
ENV_SITE_EPS_M = "FLEDERMAP_SITE_EPS_M"
ENV_SITE_MIN_POINTS = "FLEDERMAP_SITE_MIN_POINTS"


class ConfigError(Exception):
    """Required configuration is absent or invalid."""


@dataclass(frozen=True)
class Config:
    database_url: str
    archive_root: Path
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

    @classmethod
    def from_env(cls, archive_root: Path) -> Config:
        url = os.environ.get(ENV_DATABASE_URL)
        if not url:
            msg = (
                f"{ENV_DATABASE_URL} is not set. Point it at Fledermap's own "
                "database (bats_db) — never at poiidx's, which drops and "
                "recreates its tables on any config change."
            )
            raise ConfigError(msg)

        timestamp_source_raw = os.environ.get(
            ENV_TIMESTAMP_SOURCE,
            TimestampSource.FILENAME.value,
        )
        try:
            timestamp_source = TimestampSource(timestamp_source_raw)
        except ValueError as exc:
            valid = ", ".join(s.value for s in TimestampSource)
            msg = (
                f"{ENV_TIMESTAMP_SOURCE}={timestamp_source_raw!r} is not a valid "
                f"timestamp source. Valid options: {valid}."
            )
            raise ConfigError(msg) from exc

        timezone_name = os.environ.get(ENV_DEFAULT_TIMEZONE)
        if timezone_name is None:
            default_timezone: tzinfo = UTC
        else:
            try:
                default_timezone = zoneinfo.ZoneInfo(timezone_name)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
                msg = (
                    f"{ENV_DEFAULT_TIMEZONE}={timezone_name!r} is not a "
                    "known IANA timezone name (e.g. 'Europe/Berlin')."
                )
                raise ConfigError(msg) from exc

        session_gap_raw = os.environ.get(ENV_SESSION_GAP_HOURS)
        if session_gap_raw is None:
            session_gap_hours = 6.0
        else:
            try:
                session_gap_hours = float(session_gap_raw)
            except ValueError as exc:
                msg = (
                    f"{ENV_SESSION_GAP_HOURS}={session_gap_raw!r} is not a number "
                    "of hours."
                )
                raise ConfigError(msg) from exc

        site_eps_raw = os.environ.get(ENV_SITE_EPS_M)
        if site_eps_raw is None:
            site_eps_m = 75.0
        else:
            try:
                site_eps_m = float(site_eps_raw)
            except ValueError as exc:
                msg = f"{ENV_SITE_EPS_M}={site_eps_raw!r} is not a number of metres."
                raise ConfigError(msg) from exc

        site_min_points_raw = os.environ.get(ENV_SITE_MIN_POINTS)
        if site_min_points_raw is None:
            site_min_points = 3
        else:
            try:
                site_min_points = int(site_min_points_raw)
            except ValueError as exc:
                msg = (
                    f"{ENV_SITE_MIN_POINTS}={site_min_points_raw!r} is not an integer."
                )
                raise ConfigError(msg) from exc

        return cls(
            database_url=url,
            archive_root=archive_root.resolve(),
            timestamp_source=timestamp_source,
            default_timezone=default_timezone,
            session_gap_hours=session_gap_hours,
            site_eps_m=site_eps_m,
            site_min_points=site_min_points,
        )
