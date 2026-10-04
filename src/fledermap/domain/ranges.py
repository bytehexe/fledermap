"""Vocabulary for the IUCN range plausibility check (design spec
2026-10-04-fledermap-iucn-range-check-design.md). No I/O, no SQLAlchemy, so
`services/current_best.py` can type a matrix row's range without importing the
service module (which imports the job queue)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# The `taxon_code.source` under which a taxon's name in the IUCN 2022-2 layer is stored.
# Deliberately not an `IdSource`: nothing identifies a recording under it, and a CHECK/enum
# there would force every classifier-facing consumer to know about it.
RANGE_CODE_SOURCE = "iucn2022"
IUCN_RELEASE = "2022-2"
# T&C v3.1 §6 requires the citation to name the data version; §3 the non-commercial restriction.
IUCN_CITATION = f"IUCN Red List, release {IUCN_RELEASE} (non-commercial use only)"

# IUCN `presence` codes kept at fetch: 1 extant, 2 probably extant, 3 possibly extant,
# 6 presence uncertain. 4 (possibly extinct) and 5 (extinct) say nothing about where a living
# bat could be. Verified against the layer's own `legend` field.
KEPT_PRESENCE: tuple[int, ...] = (1, 2, 3, 6)
# IUCN `origin` 4: a polygon of vagrant records, not a regular range.
VAGRANT_ORIGIN = 4

FETCH_FOUND = "found"
FETCH_NOT_FOUND = "not_found"


class RangeKind(StrEnum):
    INSIDE = "inside"
    NEAR = "near"
    FAR = "far"
    NO_DATA = "no_data"


class NoDataReason(StrEnum):
    NO_LOCATION = "no_location"
    NO_CODE = "no_code"
    NOT_FOUND = "not_found"
    NOT_FETCHED = "not_fetched"


_NO_DATA_TEXT: dict[NoDataReason, str] = {
    NoDataReason.NO_LOCATION: "No range check: the recording has no location",
    NoDataReason.NO_CODE: "No range check: species not matched to an IUCN name",
    NoDataReason.NOT_FOUND: "IUCN has no range for this species",
    NoDataReason.NOT_FETCHED: "IUCN range not fetched yet",
}


def _km_text(distance_km: float) -> str:
    if distance_km < 1:
        return "less than 1 km"
    return f"{round(distance_km)} km"


@dataclass(frozen=True)
class RangeStatus:
    """The browser-safe reduction of a species' range polygons at one recording's location: a
    verdict, a distance, and a tooltip. Never carries geometry (IUCN T&C §4)."""

    kind: RangeKind
    distance_km: float | None = None
    reason: NoDataReason | None = None
    # True when the point lies only inside an `origin == 4` polygon.
    vagrant: bool = False

    def describe(self) -> str:
        if self.kind is RangeKind.INSIDE:
            return "Inside the IUCN range"
        if self.kind is RangeKind.NO_DATA:
            if self.reason is None:
                return "No IUCN range data"
            return _NO_DATA_TEXT[self.reason]
        if self.vagrant:
            base = "Only inside an IUCN vagrant-records area"
            if self.distance_km is not None and self.distance_km >= 1:
                return (
                    f"{base} ({_km_text(self.distance_km)} outside the regular range)"
                )
            return base
        text = f"{_km_text(self.distance_km or 0.0)} outside the IUCN range"
        return text[0].upper() + text[1:]
