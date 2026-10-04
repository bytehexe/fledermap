from __future__ import annotations

from fledermap.domain.ranges import NoDataReason, RangeKind, RangeStatus


def test_inside_describes_itself() -> None:
    assert RangeStatus(RangeKind.INSIDE).describe() == "Inside the IUCN range"


def test_the_only_verdicts_are_the_ones_that_report_what_we_know() -> None:
    """IUCN ranges have gaps, so there is deliberately no "outside"/"near"/"far" kind: a point
    outside the polygons is simply not confirmed, and the cell stays blank."""
    assert {kind.value for kind in RangeKind} == {"inside", "no_data", "pending"}


def test_every_no_data_reason_has_its_own_text() -> None:
    texts = {
        reason: RangeStatus(RangeKind.NO_DATA, reason=reason).describe()
        for reason in NoDataReason
    }
    assert len(set(texts.values())) == len(NoDataReason)
    assert "no location" in texts[NoDataReason.NO_LOCATION].lower()


def test_pending_is_its_own_kind_not_a_no_data_reason() -> None:
    """ "Not fetched yet" resolves by itself once the worker has run; "no data" never does, so
    the two must not share a state (or an icon)."""
    status = RangeStatus(RangeKind.PENDING)
    assert status.kind is not RangeKind.NO_DATA
    assert status.describe() == "IUCN range not fetched yet"
    assert "NOT_FETCHED" not in NoDataReason.__members__
