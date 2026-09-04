"""Query-string parsing shared by the GeoJSON API and the drawer's panel
fragment routes -- both parse the same filter shape (design spec
2026-08-26-fledermap-phase5a-drawer-design.md), and duplicating it would let
the two silently drift on a param's exact meaning (e.g. `to`'s end-of-day
handling)."""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Literal

from fledermap.domain.codes import Verdict

BBox = tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


def parse_bbox(raw: str | None) -> BBox | None:
    if raw is None:
        return None
    parts = raw.split(",")
    msg = "bbox must be 4 comma-separated numbers: min_lon,min_lat,max_lon,max_lat"
    if len(parts) != 4:
        raise ValueError(msg)
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError:
        raise ValueError(msg) from None
    return (min_lon, min_lat, max_lon, max_lat)


def parse_datetime(raw: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse a `from`/`to` query-param value.

    Deliberate, minimal INTERIM policy for the `<input type="date">` case --
    NOT a resolution of the project's open timezone question (spec D17,
    risks R1-R3), just a decision not to make it silently worse here. A bare
    `YYYY-MM-DD` value (no time component) is anchored to UTC rather than
    left naive (which would make the boundary depend on the Postgres session
    timezone against `Recording.recorded_at`'s `DateTime(timezone=True)`
    column), and when it's the `to` bound it's treated as the END of that day
    (23:59:59.999999 UTC) rather than midnight -- otherwise `to=2026-08-25`
    would silently exclude every recording from the 25th itself. A value that
    already carries a time and/or offset is left exactly as authored.
    """
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    is_bare_date = len(raw) == 10 and "T" not in raw
    if is_bare_date:
        if end_of_day:
            parsed = datetime.combine(parsed.date(), time(23, 59, 59, 999999))
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_verdict(raw: str | None) -> Verdict | Literal["all"] | None:
    if raw is None:
        return None
    if raw == "all":
        return "all"
    return Verdict(raw)


def parse_int(raw: str | None) -> int | None:
    return int(raw) if raw else None


def parse_taxon_filter(raw: str | None) -> int | Literal["unregistered"] | None:
    """The `taxon` query param: either a numeric Taxon id, or the sentinel
    "unregistered" (the map's "Unregistered species" dropdown option --
    every SPECIES verdict whose raw code never mapped to a Taxon, design
    discussion 2026-09-04). Same absent/empty-is-None convention as
    `parse_int`."""
    if not raw:
        return None
    if raw == "unregistered":
        return "unregistered"
    return int(raw)


def parse_bool(raw: str | None) -> bool:
    """Presence-based flag param (e.g. `taxon_exclude=1`) -- absent or empty
    means False, any non-empty value means True. Never raises: an
    unrecognised value like `taxon_exclude=nope` is still "set", so it's
    treated as True rather than a 400 -- there's no wrong string here the
    way there is for `taxon=notanumber`."""
    return bool(raw)


def fallback_site_label(point: tuple[float, float] | None) -> str:
    """P4-1: Site.name is unpopulated until poiidx naming ships as its own
    task -- fall back to a rounded-coordinate label rather than block on
    that unrelated integration."""
    if point is None:
        return "Site"
    lon, lat = point
    return f"{lat:.4f}, {lon:.4f}"


def detector_label(detector_key: str | None) -> str:
    """Display fallback for `Session.detector_key`, registered as the
    `detector_label` Jinja filter (`web/app.py`) and used everywhere a
    template shows a session's detector.

    `detector_key` is always `f"{make}\\x1f{serial}"` (ASCII Unit Separator,
    see `derive.sessions._detector_key`) -- never the empty string, even when
    both `make` and `serial` are blank. A plain `detector_key or 'unknown
    detector'` template expression can therefore never fall back: the raw
    control character alone makes the string truthy, so it rendered directly
    in the page instead of the intended fallback text -- visible on real
    derived sessions with a blank serial (e.g. this project's own field
    recordings) and caught only by a manual walkthrough against real data,
    since the one existing test for this covered `detector_key is None`
    (never produced by `derive.sessions`) rather than derive's real,
    always-separator-bearing output. Replacing the separator with a space and
    trimming reproduces the intended fallback for an all-blank key while
    still showing whatever real make/serial text is present.
    """
    label = (detector_key or "").replace("\x1f", " ").strip()
    return label or "unknown detector"
