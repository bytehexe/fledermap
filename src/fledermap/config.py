"""Runtime configuration.

`timestamp_source` defaults to "filename". This is a PROVISIONAL default, not a
settled decision (spec D17): the only evidence available is synthetic and
disagrees with itself by twelve hours. Revisit once real field recordings exist.
Changing it re-derives `recorded_at`; it does not require re-ingesting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fledermap.ingest.merge import TIMESTAMP_SOURCE_FILENAME, TIMESTAMP_SOURCE_METADATA

ENV_DATABASE_URL = "FLEDERMAP_DATABASE_URL"
ENV_TIMESTAMP_SOURCE = "FLEDERMAP_TIMESTAMP_SOURCE"

# The complete, known vocabulary for FLEDERMAP_TIMESTAMP_SOURCE. Anything else
# is very likely a typo (e.g. "filenmae") rather than a deliberate choice, and
# a typo here silently and permanently changes how `recorded_at` is derived
# for the whole archive with no error — see Config.from_env below.
_VALID_TIMESTAMP_SOURCES = (TIMESTAMP_SOURCE_FILENAME, TIMESTAMP_SOURCE_METADATA)


class ConfigError(Exception):
    """Required configuration is absent or invalid."""


@dataclass(frozen=True)
class Config:
    database_url: str
    archive_root: Path
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME

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

        timestamp_source = os.environ.get(
            ENV_TIMESTAMP_SOURCE,
            TIMESTAMP_SOURCE_FILENAME,
        )
        if timestamp_source not in _VALID_TIMESTAMP_SOURCES:
            valid = ", ".join(_VALID_TIMESTAMP_SOURCES)
            msg = (
                f"{ENV_TIMESTAMP_SOURCE}={timestamp_source!r} is not a valid "
                f"timestamp source. Valid options: {valid}."
            )
            raise ConfigError(msg)

        return cls(
            database_url=url,
            archive_root=archive_root.resolve(),
            timestamp_source=timestamp_source,
        )
