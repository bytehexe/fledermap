"""Pure `results_to_detections` parsing tests -- no database, no
Procrastinate task execution, mirroring the split `tests/test_batdetect2_real_api.py`
already demonstrates for the sibling classifier's own pure-parsing tests
(moved out of the db-marked `tests/test_jobs_tasks_battybirdnet.py` so they
run in the fast, non-db gate).
"""

from __future__ import annotations

from fledermap.jobs.tasks import (
    _BATTYBIRDNET_FLOOR,
    battybirdnet_lock_key,
    results_to_detections,
)


def test_battybirdnet_lock_key_is_namespaced_by_audio_hash() -> None:
    assert battybirdnet_lock_key("abc") == "battybirdnet:abc"


def test_battybirdnet_floor_is_pinned_at_point_zero_five() -> None:
    """Pins the exact value so a change to it is a deliberate, reviewed
    edit rather than a silent drift -- same reasoning as
    `tests/test_batdetect2_real_api.py`'s own pinning of `_BATDETECT2_FLOOR`
    against real model output."""
    assert _BATTYBIRDNET_FLOOR == 0.05


def test_results_to_detections_flattens_the_winner_of_each_segment() -> None:
    resolved = {"Myotis daubentonii": 7, "NOISE": None}
    results = {
        "0-3": [
            ["Myotis daubentonii_Daubentons bat", "0.9"],
            ["NOISE", "0.02"],
        ],
        "3-6": [
            ["NOISE", "0.85"],
            ["Myotis daubentonii_Daubentons bat", "0.01"],
        ],
    }

    detections = results_to_detections(
        results, lambda label: resolved.get(label.split("_")[0])
    )

    confidences = {(d.raw_label, round(d.confidence, 2)) for d in detections}
    assert confidences == {
        ("Myotis daubentonii_Daubentons bat", 0.9),
        ("NOISE", 0.85),
    }


def test_results_to_detections_keeps_only_the_top_candidate_per_segment() -> None:
    """A single fixed-width analysis window is one bat call, which can only
    be one species -- so of the several candidates BattyBirdNET scores for
    one window, only the highest-confidence one becomes a detection, even
    when a second candidate also clears the floor."""
    results = {
        "0-3": [
            ["Myotis daubentonii_Daubentons bat", "0.9"],
            ["Eptesicus serotinus_Serotine bat", "0.6"],
        ],
    }

    detections = results_to_detections(results, lambda _label: None)

    assert len(detections) == 1
    assert detections[0].raw_label == "Myotis daubentonii_Daubentons bat"
    assert detections[0].confidence == 0.9


def test_results_to_detections_resolves_the_scientific_name_prefix_only() -> None:
    def resolve_label(label: str) -> int | None:
        scientific_name = label.split("_", 1)[0]
        return 42 if scientific_name == "Myotis daubentonii" else None

    results = {"0-3": [["Myotis daubentonii_Daubentons bat", "0.9"]]}

    detections = results_to_detections(results, resolve_label)

    assert detections[0].taxon_id == 42
    assert detections[0].raw_label == "Myotis daubentonii_Daubentons bat"


def test_results_to_detections_marks_noise_as_unresolved_via_sentinel() -> None:
    """results_to_detections itself never calls sentinel_verdict -- that
    happens downstream in materialize_claims (Task 1). This test only
    proves a bare "NOISE" label is preserved verbatim as raw_label with
    taxon_id from whatever resolve_label returns for it (materialize_claims
    is what turns it into a NOISE verdict, not this parsing function)."""
    results = {"0-3": [["NOISE", "0.9"]]}

    detections = results_to_detections(results, lambda label: None)

    assert detections[0].raw_label == "NOISE"
    assert detections[0].taxon_id is None


def test_results_to_detections_keeps_a_detection_exactly_at_the_floor() -> None:
    """The floor check is `confidence < _BATTYBIRDNET_FLOOR` (strict
    less-than), so a detection at exactly the floor value is KEPT, not
    dropped -- only something strictly below it is discarded."""
    results = {"0-3": [["Myotis daubentonii_Daubentons bat", "0.05"]]}

    detections = results_to_detections(results, lambda _label: None)

    assert len(detections) == 1
    assert detections[0].confidence == 0.05


def test_results_to_detections_drops_a_detection_just_below_the_floor() -> None:
    results = {"0-3": [["Myotis daubentonii_Daubentons bat", "0.049"]]}

    detections = results_to_detections(results, lambda _label: None)

    assert detections == []


def test_results_to_detections_keeps_a_detection_just_above_the_floor() -> None:
    results = {"0-3": [["Myotis daubentonii_Daubentons bat", "0.051"]]}

    detections = results_to_detections(results, lambda _label: None)

    assert len(detections) == 1
    assert detections[0].confidence == 0.051


def test_results_to_detections_sets_geometry_from_the_window_key() -> None:
    results = {"3-6": [["Myotis daubentonii_Daubentons bat", "0.9"]]}

    detections = results_to_detections(results, lambda _label: None)

    assert len(detections) == 1
    assert detections[0].time_start_s == 3.0
    assert detections[0].time_end_s == 6.0
    assert detections[0].freq_min_hz is None
    assert detections[0].freq_max_hz is None


def test_results_to_detections_merges_consecutive_windows_sharing_the_same_label() -> (
    None
):
    results = {
        "0-3": [["Myotis daubentonii_Daubentons bat", "0.6"]],
        "3-6": [["Myotis daubentonii_Daubentons bat", "0.9"]],
        "6-9": [["Myotis daubentonii_Daubentons bat", "0.7"]],
    }

    detections = results_to_detections(results, lambda _label: None)

    assert len(detections) == 1
    assert detections[0].time_start_s == 0.0
    assert detections[0].time_end_s == 9.0
    # The merged detection's confidence is the highest of its constituent windows',
    # not an average -- see results_to_detections's own docstring for why.
    assert detections[0].confidence == 0.9


def test_results_to_detections_does_not_merge_across_a_label_change() -> None:
    results = {
        "0-3": [["Myotis daubentonii_Daubentons bat", "0.6"]],
        "3-6": [["NOISE", "0.9"]],
        "6-9": [["Myotis daubentonii_Daubentons bat", "0.7"]],
    }

    detections = results_to_detections(results, lambda _label: None)

    spans = sorted((d.time_start_s, d.time_end_s, d.raw_label) for d in detections)
    assert spans == [
        (0.0, 3.0, "Myotis daubentonii_Daubentons bat"),
        (3.0, 6.0, "NOISE"),
        (6.0, 9.0, "Myotis daubentonii_Daubentons bat"),
    ]


def test_results_to_detections_does_not_merge_across_a_dropped_window() -> None:
    """The middle window is below the floor and dropped entirely, leaving a real time
    gap between the two surviving same-label windows -- they must NOT merge, since
    nothing says the species was calling through the gap itself."""
    results = {
        "0-3": [["Myotis daubentonii_Daubentons bat", "0.6"]],
        "3-6": [["Myotis daubentonii_Daubentons bat", "0.01"]],
        "6-9": [["Myotis daubentonii_Daubentons bat", "0.7"]],
    }

    detections = results_to_detections(results, lambda _label: None)

    spans = sorted((d.time_start_s, d.time_end_s) for d in detections)
    assert spans == [(0.0, 3.0), (6.0, 9.0)]


def test_results_to_detections_merges_regardless_of_input_dict_order() -> None:
    results = {
        "6-9": [["Myotis daubentonii_Daubentons bat", "0.7"]],
        "0-3": [["Myotis daubentonii_Daubentons bat", "0.6"]],
        "3-6": [["Myotis daubentonii_Daubentons bat", "0.9"]],
    }

    detections = results_to_detections(results, lambda _label: None)

    assert len(detections) == 1
    assert detections[0].time_start_s == 0.0
    assert detections[0].time_end_s == 9.0
