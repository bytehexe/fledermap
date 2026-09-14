"""Timezone-aware timestamp rendering for Jinja templates (design spec
docs/superpowers/specs/2026-09-14-fledermap-timezone-display-design.md).

Every `DateTime(timezone=True)` column comes back from SQLAlchemy already
converted to whatever the Postgres session's `TimeZone` setting is (see the
spec's Problem section for why that's Europe/Berlin on this machine, and why
the *original* per-recording device offset does not survive that round
trip). These functions re-attach a real IANA `ZoneInfo` -- via `astimezone()`,
which changes no instant, only the tzinfo object -- so `%Z` reports the
correct DST-aware abbreviation (CEST vs. CET) for that specific date, rather
than a value cached once and never revisited.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def local_datetime(dt: datetime | None, tz: ZoneInfo) -> str:
    """'2026-09-06 20:29 CEST'. No current caller passes `None` -- every
    template today uses this on a non-nullable timestamp -- but the `None`
    branch exists so a future nullable timestamp column (e.g.
    `Recording.filename_at`, `MergeProposal.resolved_at`) renders an em dash
    through this filter rather than the literal string 'None' if one is ever
    passed through it."""
    if dt is None:
        return "—"
    local = dt.astimezone(tz)
    return f"{local:%Y-%m-%d %H:%M %Z}"


def local_datetime_seconds(dt: datetime | None, tz: ZoneInfo) -> str:
    """'2026-09-06 20:29:53 CEST' -- like local_datetime but with seconds.
    Used only where a user distinguishes one recording from a near-identical
    neighbor recorded seconds apart (recording_details.html, the drawer
    panel's recording metadata) -- everywhere else, minute resolution is
    enough and local_datetime is used instead."""
    if dt is None:
        return "—"
    local = dt.astimezone(tz)
    return f"{local:%Y-%m-%d %H:%M:%S %Z}"


def local_date(dt: datetime | None, tz: ZoneInfo) -> str:
    """'2026-09-06' -- no zone suffix. Used only for `sites_list.html`'s
    date-only column, where a bare calendar date reads fine without one and
    there's no room for it in that table."""
    if dt is None:
        return "—"
    return f"{dt.astimezone(tz):%Y-%m-%d}"
