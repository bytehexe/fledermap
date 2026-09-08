# tests/test_statistics_diversity_estimators.py
"""Pure unit coverage for services/statistics.py's Chao1 estimated-richness
and Good-Turing sample-coverage helpers -- no database needed, kept out of
test_statistics_query.py (module-level `pytestmark = pytest.mark.db`) the
same way test_statistics_view_series_json.py split _series_json out of the
db-marked view test file.

Formulas (docs/superpowers/specs/2026-09-05-fledermap-statistics-design.md's
"Open follow-ups" section, implemented directly rather than via the
candidate `hillrep` library -- see the module docstring in statistics.py for
why): f1/f2 are the counts of taxa detected exactly once/twice ("singletons"/
"doubletons"), n is the total individual count.

Chao1: S_est = S_obs + f1^2/(2*f2) if f2 > 0, else S_obs + f1*(f1-1)/2
(the bias-corrected variant for f2 == 0, per Chao 1987).

Sample coverage (Good-Turing): C = 1 - f1/n -- the fraction of individuals
belonging to a species already seen at least once more than once."""

from __future__ import annotations

from fledermap.services.statistics import _chao1_richness, _sample_coverage


def test_chao1_richness_matches_observed_when_no_singletons() -> None:
    # Every taxon seen 3+ times: no unseen-species signal, so the estimate
    # equals the observed count.
    counts = {1: 3, 2: 5, 3: 4}
    assert _chao1_richness(counts) == 3


def test_chao1_richness_adds_the_standard_bias_correction() -> None:
    # S_obs=3, f1 (count==1) = 2 (taxa 2 and 3), f2 (count==2) = 1 (taxon 4).
    counts = {1: 10, 2: 1, 3: 1, 4: 2}
    # S_est = 4 + 2^2/(2*1) = 4 + 2 = 6
    assert _chao1_richness(counts) == 6.0


def test_chao1_richness_uses_the_f2_zero_correction() -> None:
    # No doubletons at all (f2=0): S_est = S_obs + f1*(f1-1)/2, avoiding a
    # division by zero.
    counts = {1: 10, 2: 1, 3: 1, 4: 1}
    # S_obs=4, f1=3 (taxa 2,3,4), f2=0 -> S_est = 4 + 3*2/2 = 7
    assert _chao1_richness(counts) == 7.0


def test_chao1_richness_of_empty_counts_is_zero() -> None:
    assert _chao1_richness({}) == 0.0


def test_sample_coverage_is_one_when_no_singletons() -> None:
    counts = {1: 3, 2: 5, 3: 4}
    assert _sample_coverage(counts) == 1.0


def test_sample_coverage_drops_with_more_singletons() -> None:
    # n = 10+1+1+2 = 14, f1 = 2 -> C = 1 - 2/14
    counts = {1: 10, 2: 1, 3: 1, 4: 2}
    assert _sample_coverage(counts) == 1 - 2 / 14


def test_sample_coverage_of_empty_counts_is_zero() -> None:
    assert _sample_coverage({}) == 0.0
