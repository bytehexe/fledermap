from __future__ import annotations

from fledermap.domain.ranges import NoDataReason, RangeKind, RangeStatus


def test_inside_describes_itself() -> None:
    assert RangeStatus(RangeKind.INSIDE).describe() == "Inside the IUCN range"


def test_near_and_far_state_the_rounded_distance() -> None:
    assert (
        RangeStatus(RangeKind.NEAR, distance_km=87.4).describe()
        == "87 km outside the IUCN range"
    )
    assert (
        RangeStatus(RangeKind.FAR, distance_km=640.6).describe()
        == "641 km outside the IUCN range"
    )


def test_a_sub_kilometre_distance_is_not_shown_as_zero() -> None:
    assert (
        RangeStatus(RangeKind.NEAR, distance_km=0.3).describe()
        == "Less than 1 km outside the IUCN range"
    )


def test_vagrant_only_area_is_described_as_such() -> None:
    status = RangeStatus(RangeKind.NEAR, distance_km=0.0, vagrant=True)
    assert status.describe() == "Only inside an IUCN vagrant-records area"
    far_from_regular = RangeStatus(RangeKind.NEAR, distance_km=212.0, vagrant=True)
    assert far_from_regular.describe() == (
        "Only inside an IUCN vagrant-records area (212 km outside the regular range)"
    )


def test_every_no_data_reason_has_its_own_text() -> None:
    texts = {
        reason: RangeStatus(RangeKind.NO_DATA, reason=reason).describe()
        for reason in NoDataReason
    }
    assert len(set(texts.values())) == len(NoDataReason)
    assert "no location" in texts[NoDataReason.NO_LOCATION].lower()
    assert "not fetched" in texts[NoDataReason.NOT_FETCHED].lower()
