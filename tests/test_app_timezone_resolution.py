"""Unit tests for `web/app.py`'s `_resolve_display_timezone` -- pulled out of
`create_app` specifically so the "Postgres reports a zone name Python's
zoneinfo doesn't recognize" error path can be exercised without a real
Postgres connection (see docs/superpowers/specs/
2026-09-14-fledermap-timezone-display-design.md). Not db-marked."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from fledermap.web.app import _resolve_display_timezone


def test_resolve_display_timezone_returns_a_zoneinfo_for_a_valid_name() -> None:
    assert _resolve_display_timezone("Europe/Berlin") == ZoneInfo("Europe/Berlin")


def test_resolve_display_timezone_raises_runtime_error_for_an_unrecognized_name() -> (
    None
):
    with pytest.raises(RuntimeError, match="current_setting"):
        _resolve_display_timezone("Not/A_Real_Zone")
