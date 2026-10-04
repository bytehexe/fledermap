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


# There is deliberately no "outside"/"near"/"far" kind: IUCN ranges have gaps and lag behind
# range expansions, so a point outside them is unconfirmed, not implausible
# (`services/iucn_ranges.range_status` returns no status for it).
class RangeKind(StrEnum):
    INSIDE = "inside"
    # The check cannot answer for this species and never will without a change in the data
    # (no location, no IUCN name, IUCN has no range).
    NO_DATA = "no_data"
    # The answer is merely not there YET: the worker has not fetched this species' range.
    # Its own kind, not a NO_DATA reason, because it resolves by itself and so must not look
    # like the permanent "no data" state.
    PENDING = "pending"


class NoDataReason(StrEnum):
    NO_LOCATION = "no_location"
    NO_CODE = "no_code"
    NOT_FOUND = "not_found"


_NO_DATA_TEXT: dict[NoDataReason, str] = {
    NoDataReason.NO_LOCATION: "No range check: the recording has no location",
    NoDataReason.NO_CODE: "No range check: species not matched to an IUCN name",
    NoDataReason.NOT_FOUND: "IUCN has no range for this species",
}


@dataclass(frozen=True)
class RangeStatus:
    """The browser-safe reduction of a species' range polygons at one recording's location: a
    verdict and a tooltip. Never carries geometry (IUCN T&C §4)."""

    kind: RangeKind
    reason: NoDataReason | None = None

    def describe(self) -> str:
        if self.kind is RangeKind.INSIDE:
            return "Inside the IUCN range"
        if self.kind is RangeKind.PENDING:
            return "IUCN range not fetched yet"
        if self.reason is None:
            return "No IUCN range data"
        return _NO_DATA_TEXT[self.reason]
