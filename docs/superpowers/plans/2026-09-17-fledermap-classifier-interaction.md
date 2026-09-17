# Classifier Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded per-source special cases in `current_best_identification`
(precedence walk) and `_disagreement_reason` (review-flag voting) with a declarative
`is_manual`/`is_only_noise`/`is_multi_support` property model on `IdSource`, so a future
classifier participates correctly by declaring its properties instead of needing new code in
either function.

**Architecture:** One new small property-lookup module addition in `domain/codes.py` (three
frozenset-backed predicate functions), consumed by two independent service-layer rewrites:
`current_best.py`'s precedence walk gets a "peek past a plain winner for a matching
multi-support source below it" step, and `review_flags.py`'s disagreement check moves from a
flat "≥2 distinct claim keys" scan to per-source vote-sets compared pairwise under an
equality/containment rule that depends on `is_multi_support`.

**Tech Stack:** Python 3.12, SQLAlchemy ORM models (`Identification`, `Recording`), pytest
(`hatch test`), mypy (`hatch run types:check`).

**Spec:** `docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md`

## Global Constraints

- `_PRECEDENCE`'s order in `current_best.py` does not change (spec §2, §6) — this plan only
  changes how sources already in that order interact.
- A `multi_support` source's live claim set for one recording is always either exactly
  `{NO_ID}` or one-or-more `SPECIES`/sentinel-verdict claims, never mixed (spec §5) — every
  containment/equality check below relies on this and does not need to guard against a mixed
  set.
- `EMT_MANUAL` moves from "weak NoID" (falls through like an automatic classifier) to "strong
  NoID" (wins outright, matching `MANUAL`) — a deliberate behavior change (spec §2/§3.1), not a
  bug to avoid.
- The multi-support-vs-multi-support disagreement rule is **exact equality**, not subset —
  spec §4 and its non-goals section (§6) are explicit that weakening this to a subset check is
  future work, not part of this plan.
- Test file conventions already established in this codebase: `tests/test_codes.py`,
  `tests/test_current_best.py`, `tests/test_review_flags.py` use plain (non-DB) pytest tests
  with local `_recording`/`_ident` helper builders — follow the same style, do not introduce a
  new DB-marked test for anything this plan touches (none of it needs the database).

---

## Task 1: Classifier property model (`domain/codes.py`)

**Files:**
- Modify: `src/fledermap/domain/codes.py`
- Test: `tests/test_codes.py`

**Interfaces:**
- Produces: `is_manual(source: IdSource) -> bool`, `is_only_noise(source: IdSource) -> bool`,
  `is_multi_support(source: IdSource) -> bool` — importable from `fledermap.domain.codes`.
  Tasks 2 and 3 both import all three.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codes.py`:

```python
def test_is_manual_true_for_manual_and_emt_manual() -> None:
    assert is_manual(IdSource.MANUAL) is True
    assert is_manual(IdSource.EMT_MANUAL) is True


def test_is_manual_false_for_automatic_sources() -> None:
    assert is_manual(IdSource.EMT_GUANO) is False
    assert is_manual(IdSource.BATDETECT2) is False
    assert is_manual(IdSource.FLEDERMAP_NOISE) is False


def test_is_only_noise_true_only_for_fledermap_noise() -> None:
    assert is_only_noise(IdSource.FLEDERMAP_NOISE) is True
    assert is_only_noise(IdSource.BATDETECT2) is False
    assert is_only_noise(IdSource.MANUAL) is False


def test_is_multi_support_true_for_manual_batdetect2_battybirdnet() -> None:
    assert is_multi_support(IdSource.MANUAL) is True
    assert is_multi_support(IdSource.BATDETECT2) is True
    assert is_multi_support(IdSource.BATTYBIRDNET) is True


def test_is_multi_support_false_for_plain_sources() -> None:
    assert is_multi_support(IdSource.EMT_MANUAL) is False
    assert is_multi_support(IdSource.EMT_GUANO) is False
    assert is_multi_support(IdSource.EMT_WAMD) is False
    assert is_multi_support(IdSource.EMT_FILENAME) is False
    assert is_multi_support(IdSource.FLEDERMAP_NOISE) is False
```

Add the import at the top of the same file:

```python
from fledermap.domain.codes import (
    IdSource,
    Verdict,
    is_manual,
    is_multi_support,
    is_only_noise,
    sentinel_verdict,
)
```

(replacing the existing `from fledermap.domain.codes import Verdict, sentinel_verdict` line).

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_codes.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_manual' from 'fledermap.domain.codes'`
(or the equivalent for `is_multi_support`/`is_only_noise`).

- [ ] **Step 3: Implement the property model**

In `src/fledermap/domain/codes.py`, add after the `IdSource` class definition (i.e. after its
closing `FLEDERMAP_NOISE = "fledermap.noise"` line and before `class VisualSighting`):

```python
# Declarative classifier properties -- consumed by services/current_best.py's precedence walk
# and services/review_flags.py's disagreement check, so a future classifier participates
# correctly by declaring its properties here instead of needing new code in either function
# (docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md §2).

_MANUAL_SOURCES: frozenset[IdSource] = frozenset({IdSource.MANUAL, IdSource.EMT_MANUAL})

_ONLY_NOISE_SOURCES: frozenset[IdSource] = frozenset({IdSource.FLEDERMAP_NOISE})

_MULTI_SUPPORT_SOURCES: frozenset[IdSource] = frozenset(
    {IdSource.MANUAL, IdSource.BATDETECT2, IdSource.BATTYBIRDNET},
)


def is_manual(source: IdSource) -> bool:
    """A human's deliberate judgment -- either in-app (`MANUAL`) or the on-device correction
    re-derived from the file (`EMT_MANUAL`). Its own `NO_ID` is a considered "I checked and
    there's nothing here," not an admission of uncertainty, so `current_best.py`'s precedence
    walk treats it as "strong NoID" (wins outright) rather than falling through like an
    automatic classifier's `NO_ID` does. Also excluded entirely from
    `review_flags.py`'s disagreement voting."""
    return source in _MANUAL_SOURCES


def is_only_noise(source: IdSource) -> bool:
    """A source whose only possible verdicts are `NOISE` and `NO_ID` -- it can never assert a
    species. Its `NO_ID` is excluded from `review_flags.py`'s disagreement voting (an admission
    from a coarse pre-filter that it can't tell either way is not informative alongside a real
    classifier's confident species call); its `NOISE` claim is not excluded."""
    return source in _ONLY_NOISE_SOURCES


def is_multi_support(source: IdSource) -> bool:
    """A source whose claims for one recording may legitimately name more than one taxon at
    once, rather than every extra claim past the first being either a competing alternative or
    an error. Drives both `current_best.py`'s multi-support containment override and
    `review_flags.py`'s equality/containment disagreement rule."""
    return source in _MULTI_SUPPORT_SOURCES
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_codes.py -v`
Expected: PASS (10 tests: the 5 pre-existing plus the 5 new ones).

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check`
Expected: no errors.

Run: `hatch fmt`
Expected: no changes needed (or auto-fixes applied cleanly).

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/domain/codes.py tests/test_codes.py
git commit -m "$(cat <<'EOF'
feat: add is_manual/is_only_noise/is_multi_support classifier properties

Declarative property model on IdSource, replacing the hardcoded
_HUMAN_REVIEW_SOURCES/source-name checks that current_best.py and
review_flags.py each maintained independently.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Precedence walk -- strong NoID + multi-support containment override (`current_best.py`)

**Files:**
- Modify: `src/fledermap/services/current_best.py`
- Test: `tests/test_current_best.py`

**Interfaces:**
- Consumes: `is_manual`, `is_multi_support` from `fledermap.domain.codes` (Task 1).
- Produces: `current_best_identification`'s external behavior changes (see below); its
  signature (`(recording: Recording) -> CurrentIdentification | None`) is unchanged, so no
  caller elsewhere in the codebase needs touching.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_current_best.py`:

```python
def test_emt_manual_no_id_is_active_and_shadows_a_lower_precedence_species() -> None:
    """Unlike before this change, EMT_MANUAL's own NO_ID now wins outright (strong NoID),
    matching MANUAL -- see spec 2026-09-17-fledermap-classifier-interaction-design.md §2/§3.1."""
    r = _recording(
        _ident(IdSource.EMT_MANUAL, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.EMT_GUANO, taxon_id=9),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_MANUAL
    assert best.verdict == Verdict.NO_ID


def test_multi_support_override_when_higher_precedence_species_is_contained() -> None:
    """EMT_GUANO (plain, higher precedence) calls PIPPIP; BATDETECT2 (multi_support, lower
    precedence) calls {PIPPIP, MYODAU}. Since PIPPIP is in BATDETECT2's set, the richer
    multi-species result wins instead of narrowing to EMT_GUANO's single claim."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})
    assert all(c.source == IdSource.BATDETECT2 for c in best.claims)


def test_no_override_when_higher_precedence_species_is_not_contained() -> None:
    """EMT_GUANO calls a taxon BATDETECT2's set doesn't contain -- a real disagreement, so
    EMT_GUANO's claim stands untouched (the review-flag rewrite in Task 3 is what surfaces this
    as "Classifiers disagree")."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=3),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert not best.is_multi
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 3


def test_multi_support_override_skips_over_a_weak_no_id_source_in_between() -> None:
    """EMT_WAMD sits between EMT_GUANO and BATDETECT2 in precedence and has only an automatic
    NO_ID claim (skipped, "weak") -- the peek must not stop there and must still reach
    BATDETECT2 below it."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1),
        _ident(IdSource.EMT_WAMD, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})


def test_no_peek_when_the_winner_is_manual() -> None:
    """A human's call is never second-guessed by a multi_support classifier below it, even when
    that classifier's set would have contained the human's taxon."""
    r = _recording(
        _ident(IdSource.EMT_MANUAL, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert not best.is_multi
    assert best.primary.source == IdSource.EMT_MANUAL
    assert best.primary.taxon_id == 1


def test_no_peek_when_the_winner_verdict_is_noise() -> None:
    """A multi_support source's set is always species-only or {NO_ID} (spec §5), so a NOISE
    winner can never be contained in one -- the peek is skipped as pure overhead, and the NOISE
    verdict stands."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, verdict=Verdict.NOISE, taxon_id=None),
        _ident(IdSource.BATDETECT2, taxon_id=1),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.verdict == Verdict.NOISE


def test_multi_support_winner_returned_directly_without_peeking_further() -> None:
    """When the FIRST source with live claims is itself multi_support, it wins outright --
    nothing below it (even another multi_support source) is ever consulted."""
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
        _ident(IdSource.BATTYBIRDNET, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})


def test_multi_support_override_also_works_with_battybirdnet() -> None:
    """The containment override is not hardcoded to BATDETECT2 -- BATTYBIRDNET (the other
    multi_support automatic classifier) triggers it the same way."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1),
        _ident(IdSource.BATTYBIRDNET, taxon_id=1),
        _ident(IdSource.BATTYBIRDNET, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})
    assert all(c.source == IdSource.BATTYBIRDNET for c in best.claims)


def test_identification_status_shadowed_for_a_species_overridden_by_containment() -> None:
    """EMT_GUANO's own claim loses to the multi-support override -- it must show as
    "shadowed" (a real claim that lost), not "current" or "passive"."""
    guano = _ident(IdSource.EMT_GUANO, taxon_id=1)
    batdetect2_a = _ident(IdSource.BATDETECT2, taxon_id=1)
    batdetect2_b = _ident(IdSource.BATDETECT2, taxon_id=2)
    r = _recording(guano, batdetect2_a, batdetect2_b)
    best = current_best_identification(r)

    assert identification_status(guano, best) == "shadowed"
    assert identification_status(batdetect2_a, best) == "current"
    assert identification_status(batdetect2_b, best) == "current"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_current_best.py -v`
Expected: FAIL — `test_emt_manual_no_id_is_active_and_shadows_a_lower_precedence_species`
fails because `EMT_MANUAL`'s `NO_ID` still falls through today; the multi-support override tests
fail because `BATDETECT2`'s/`BATTYBIRDNET`'s second claim is never considered (today's walk
returns `EMT_GUANO`'s single claim as `best`, so `best.is_multi` is `False` where the tests
expect `True`, or `best.primary.source` doesn't match).

- [ ] **Step 3: Implement the walk changes**

Replace the `_PRECEDENCE`/`_EPOCH` header comment block through the end of
`current_best_identification` (roughly lines 1–98 of the current file) with:

Old (module docstring, imports, and `current_best_identification` — the parts that change):

```python
from fledermap.domain.codes import IdSource, Verdict
```

New:

```python
from fledermap.domain.codes import IdSource, Verdict, is_manual, is_multi_support
```

Old:

```python
def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Walk sources in precedence order. A source's claims are skipped
    (fall through to the next source) only when they are ALL automatic
    NO_ID -- any SPECIES, NOISE, or MANUAL claim of any verdict wins
    outright and stops the walk.

    Only MANUAL may surface more than one claim (a genuine multi-species
    file) -- every other source has at most one live row per recording by
    construction (services/identifications.py's replace_claims keeps it that
    way), so there is nothing to dedupe here any more."""
    for source in _PRECEDENCE:
        matches = [i for i in recording.identifications if i.source == source]
        if not matches:
            continue
        if source != IdSource.MANUAL and all(
            m.verdict == Verdict.NO_ID for m in matches
        ):
            continue
        return CurrentIdentification.from_matches(matches)
    return None
```

New:

```python
def _live_claims(recording: Recording, source: IdSource) -> list[Identification]:
    """A source's claims for this recording, or an empty list when its claims are ALL
    automatic NO_ID ("weak NoID" -- falls through to the next source). `is_manual` sources
    never fall through ("strong NoID": a human's own NO_ID is a considered answer, not an
    admission of uncertainty) -- see domain/codes.py's `is_manual` docstring."""
    matches = [i for i in recording.identifications if i.source == source]
    if not matches:
        return []
    if not is_manual(source) and all(m.verdict == Verdict.NO_ID for m in matches):
        return []
    return matches


def _multi_support_containment_override(
    recording: Recording,
    winning_taxon_id: int | None,
    remaining_sources: Sequence[IdSource],
) -> list[Identification] | None:
    """Looks past a plain (non-multi_support, non-manual) SPECIES winner for the NEAREST
    multi_support source below it with live claims -- skipping any weak-NoID source along the
    way via `_live_claims`, same as the main walk. Returns that source's full claim set when it
    contains `winning_taxon_id` (no real disagreement, so show the richer result instead of
    discarding it); returns None otherwise (either no multi_support source was found, or it
    doesn't contain the taxon -- a real disagreement, so the original winner stands and nothing
    further below is consulted, per spec §3.2: "the highest precedence source that actually
    disagrees is the one that should win")."""
    for source in remaining_sources:
        if not is_multi_support(source):
            continue
        matches = _live_claims(recording, source)
        if not matches:
            continue
        taxon_ids = {m.taxon_id for m in matches if m.taxon_id is not None}
        if winning_taxon_id in taxon_ids:
            return matches
        return None
    return None


def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Walk sources in precedence order. A source's claims are skipped (fall through to the
    next source) per `_live_claims` above -- any SPECIES, NOISE, or is_manual claim of any
    verdict wins outright and stops the walk, EXCEPT that a plain (non-`is_manual`,
    non-`is_multi_support`) SPECIES winner is first checked against the nearest
    `is_multi_support` source below it (`_multi_support_containment_override`): if that
    source's claim set contains the winner's taxon, its full (possibly multi-species) result
    replaces the plain winner instead of discarding the extra species information. A `NOISE`
    winner never triggers this check -- a `multi_support` source's set is always species-only or
    `{NO_ID}` (spec §5), so containment against `NOISE` can never apply. Nor does an `is_manual`
    winner -- a human's call, or a device-side manual correction, is never second-guessed by an
    automatic classifier below it.

    `is_manual` and `is_multi_support` sources may surface more than one claim (a genuine
    multi-species file, or the containment override above) -- every plain source has at most one
    live row per recording by construction (services/identifications.py's replace_claims keeps
    it that way)."""
    for index, source in enumerate(_PRECEDENCE):
        matches = _live_claims(recording, source)
        if not matches:
            continue
        if (
            not is_manual(source)
            and not is_multi_support(source)
            and matches[0].verdict == Verdict.SPECIES
        ):
            override = _multi_support_containment_override(
                recording,
                matches[0].taxon_id,
                _PRECEDENCE[index + 1 :],
            )
            if override is not None:
                return CurrentIdentification.from_matches(override)
        return CurrentIdentification.from_matches(matches)
    return None
```

Also update the `CurrentIdentification` class docstring and its `primary` property docstring,
which currently claim only `MANUAL` can have more than one claim:

Old:

```python
@dataclass(frozen=True)
class CurrentIdentification:
    """One or more non-superseded claims from the single winning source
    (§2/§3 of the design spec). `claims` has more than one entry only when
    the winning source is MANUAL with multiple SPECIES/group claims -- every
    other source is resolved to exactly one claim, same as before this
    rewrite."""

    claims: tuple[Identification, ...]

    @property
    def is_multi(self) -> bool:
        return len(self.claims) > 1

    @property
    def primary(self) -> Identification:
        """The single representative claim for headline/marker-color
        purposes -- first-added (lowest first_seen_at). This tie-break is
        only reachable for a genuine multi-species MANUAL result: every
        other source has at most one live row per recording by construction
        (services/identifications.py's replace_claims), so there is nothing
        to tie-break there any more."""
        return min(self.claims, key=lambda i: i.first_seen_at or _EPOCH)
```

New:

```python
@dataclass(frozen=True)
class CurrentIdentification:
    """One or more non-superseded claims from the single winning source (§2/§3 of the design
    spec, extended by docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md).
    `claims` has more than one entry only when the winning source is `is_multi_support`
    (MANUAL/BATDETECT2/BATTYBIRDNET) with multiple SPECIES/group claims -- every plain source is
    resolved to exactly one claim."""

    claims: tuple[Identification, ...]

    @property
    def is_multi(self) -> bool:
        return len(self.claims) > 1

    @property
    def primary(self) -> Identification:
        """The single representative claim for headline/marker-color purposes --
        first-added (lowest first_seen_at). This tie-break is only reachable for a genuine
        multi-species `is_multi_support` result: every plain source has at most one live row
        per recording by construction (services/identifications.py's replace_claims), so there
        is nothing to tie-break there any more."""
        return min(self.claims, key=lambda i: i.first_seen_at or _EPOCH)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_current_best.py -v`
Expected: PASS (all pre-existing tests plus the 9 new ones from Step 1).

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check`
Expected: no errors.

Run: `hatch fmt`
Expected: no changes needed (or auto-fixes applied cleanly).

- [ ] **Step 6: Run the full fast test suite**

Run: `hatch test -m "not db"`
Expected: PASS — confirms nothing else in the codebase (e.g. `web/views`, `services/review_flags.py`
before Task 3) broke from this change. `services/review_flags.py`'s own tests are expected to
still pass at this point since Task 3 hasn't touched it yet.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/services/current_best.py tests/test_current_best.py
git commit -m "$(cat <<'EOF'
feat: multi-support containment override in current_best precedence walk

EMT_MANUAL's own NO_ID now wins outright (strong NoID, matching MANUAL).
A plain source's higher-precedence SPECIES claim no longer discards a
lower-precedence multi_support source's result when the claim is
contained in it -- the richer multi-species answer is shown instead.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Per-source pairwise disagreement voting (`review_flags.py`)

**Files:**
- Modify: `src/fledermap/services/review_flags.py`
- Test: `tests/test_review_flags.py`

**Interfaces:**
- Consumes: `is_manual`, `is_multi_support`, `is_only_noise` from `fledermap.domain.codes`
  (Task 1); `_claim_key` (already defined in `review_flags.py`, unchanged).
- Produces: `_disagreement_reason`'s external behavior changes (see below); its signature
  (`(recording: Recording) -> str | None`) is unchanged.

- [ ] **Step 1: Update the now-outdated test and add the new ones**

`test_disagreement_reason_none_when_the_other_claim_is_no_id` currently asserts that a plain
automatic source's `NO_ID` never counts as disagreement. That is the exact gap this task fixes
(spec §1 gap 3) — replace it:

Old:

```python
def test_disagreement_reason_none_when_the_other_claim_is_no_id() -> None:
    """NO_ID never counts as disagreement -- it's not a real answer."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1, verdict=Verdict.SPECIES),
        _ident(IdSource.BATDETECT2, taxon_id=None, verdict=Verdict.NO_ID),
    )
    assert _disagreement_reason(r) is None
```

New:

```python
def test_disagreement_reason_fires_when_a_plain_no_id_meets_a_species_claim() -> None:
    """A plain automatic classifier's confident NO_ID now counts as a vote -- it disagrees with
    another classifier's confident SPECIES call, unless the NO_ID came from an is_only_noise
    source (see the next test)."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1, verdict=Verdict.SPECIES),
        _ident(IdSource.EMT_WAMD, taxon_id=None, verdict=Verdict.NO_ID),
    )
    assert _disagreement_reason(r) is not None


def test_disagreement_reason_none_when_an_only_noise_sources_no_id_meets_a_species_claim() -> (
    None
):
    """FLEDERMAP_NOISE's NO_ID is excluded from voting entirely -- a coarse pre-filter admitting
    it can't tell either way is not informative alongside a real classifier's species call."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1, verdict=Verdict.SPECIES),
        _ident(IdSource.FLEDERMAP_NOISE, taxon_id=None, verdict=Verdict.NO_ID),
    )
    assert _disagreement_reason(r) is None


def test_disagreement_reason_none_when_two_plain_no_ids_agree() -> None:
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=None, verdict=Verdict.NO_ID),
        _ident(IdSource.EMT_WAMD, taxon_id=None, verdict=Verdict.NO_ID),
    )
    assert _disagreement_reason(r) is None


def test_disagreement_reason_none_when_a_plain_species_is_contained_in_a_multi_support_set() -> (
    None
):
    """A plain source's single call agrees with a multi_support source's set as long as the
    call is a member of it -- containment, not equality."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )
    assert _disagreement_reason(r) is None


def test_disagreement_reason_fires_when_a_plain_species_is_not_in_a_multi_support_set() -> None:
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=3),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )
    assert _disagreement_reason(r) is not None


def test_disagreement_reason_none_when_two_multi_support_sets_are_exactly_equal() -> None:
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
        _ident(IdSource.BATTYBIRDNET, taxon_id=1),
        _ident(IdSource.BATTYBIRDNET, taxon_id=2),
    )
    assert _disagreement_reason(r) is None


def test_disagreement_reason_fires_when_two_multi_support_sets_are_not_exactly_equal() -> None:
    """Deliberately strict (spec §4): two multi_support sources disagree whenever their sets
    aren't identical, even when one is a strict subset of the other."""
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
        _ident(IdSource.BATTYBIRDNET, taxon_id=1),
    )
    assert _disagreement_reason(r) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_review_flags.py -v`
Expected: FAIL —
`test_disagreement_reason_fires_when_a_plain_no_id_meets_a_species_claim`,
`test_disagreement_reason_fires_when_a_plain_species_is_not_in_a_multi_support_set`, and
`test_disagreement_reason_fires_when_two_multi_support_sets_are_not_exactly_equal` fail because
today's flat key-based check either excludes NO_ID entirely or doesn't do a containment/equality
comparison at all (it would currently see 2 distinct keys among `{taxon 1}`/`{taxon 3}` and
flag correctly by accident in the "not contained" case, but not in the containment-should-agree
case above it, nor in the multi-vs-multi cases). Confirm the failures point at these specific
assertions before proceeding.

- [ ] **Step 3: Implement the pairwise vote-set rewrite**

In `src/fledermap/services/review_flags.py`, update the import line:

Old:

```python
from fledermap.domain.codes import IdSource, Verdict
```

New:

```python
from fledermap.domain.codes import IdSource, Verdict, is_manual, is_multi_support, is_only_noise
```

Replace `_disagreement_reason` (and the `_HUMAN_REVIEW_SOURCES`-based exclusion it uses) with a
per-source vote-set version. Old:

```python
def _disagreement_reason(recording: Recording) -> str | None:
    """At least two classifiers disagreeing on verdict or species (Obsidian
    backlog: a new "flagged for review" criterion, built with future
    classifiers in mind). Deliberately names no `IdSource` explicitly --
    every source with a live, non-NO_ID, non-human claim contributes one
    vote, so a future classifier participates automatically with no code
    change here.

    NO_ID never counts as disagreement (an admission of uncertainty isn't
    an answer to disagree with); MANUAL/EMT_MANUAL claims are excluded
    entirely, matching `_has_human_review`'s gate -- called directly (not
    just via `review_reasons`), this function must not treat a human's
    claim as just another classifier's vote."""
    keys = {
        _claim_key(ident)
        for ident in recording.identifications
        if ident.source not in _HUMAN_REVIEW_SOURCES and ident.verdict != Verdict.NO_ID
    }
    if len(keys) >= 2:
        return "Classifiers disagree"
    return None
```

New:

```python
_ClaimKey = tuple[Verdict] | tuple[Verdict, int | str]


def _source_vote_sets(
    recording: Recording,
) -> dict[IdSource, frozenset[_ClaimKey]]:
    """One vote-set per eligible source -- `is_manual` sources are excluded entirely (matching
    `_HUMAN_REVIEW_SOURCES`'s old role; called directly, not just via `review_reasons`, this
    function must not treat a human's claim as just another classifier's vote), and an
    `is_only_noise` source's `NO_ID` claim is excluded (a coarse pre-filter admitting it can't
    tell either way is not informative -- see domain/codes.py's `is_only_noise` docstring). A
    source with no remaining claims after that filtering contributes no vote at all."""
    by_source: dict[IdSource, list[Identification]] = {}
    for ident in recording.identifications:
        if is_manual(ident.source):
            continue
        by_source.setdefault(ident.source, []).append(ident)

    votes: dict[IdSource, frozenset[_ClaimKey]] = {}
    for source, claims in by_source.items():
        if is_only_noise(source):
            claims = [c for c in claims if c.verdict != Verdict.NO_ID]
        if not claims:
            continue
        votes[source] = frozenset(_claim_key(c) for c in claims)
    return votes


def _compatible(
    source_a: IdSource,
    set_a: frozenset[_ClaimKey],
    source_b: IdSource,
    set_b: frozenset[_ClaimKey],
) -> bool:
    """Whether two sources' vote-sets agree, per spec §4:

    - Both `is_multi_support`: only exact equality agrees -- deliberately strict (see the
      module-level design spec's §4/§6): two multi-species sources must name exactly the same
      set, even a strict subset of the other's calls counts as disagreement.
    - Exactly one `is_multi_support`: the plain source's single call must be a MEMBER of the
      multi_support source's set (containment, not equality) -- a source that can only ever
      name one species isn't expected to name every species a multi-species source might.
    - Neither `is_multi_support`: both sets are singletons, so equality and containment
      coincide -- same comparison the original flat key-based check already made for two plain
      sources."""
    a_multi = is_multi_support(source_a)
    b_multi = is_multi_support(source_b)
    if a_multi and b_multi:
        return set_a == set_b
    if a_multi:
        return set_b <= set_a
    if b_multi:
        return set_a <= set_b
    return set_a == set_b


def _disagreement_reason(recording: Recording) -> str | None:
    """At least two classifiers disagreeing on verdict or species (Obsidian backlog: a new
    "flagged for review" criterion, built with future classifiers in mind, extended by
    docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md §4).
    Deliberately names no `IdSource` explicitly beyond the `is_manual`/`is_only_noise`/
    `is_multi_support` properties -- a future classifier participates automatically by
    declaring those, with no code change here."""
    votes = list(_source_vote_sets(recording).items())
    for i in range(len(votes)):
        source_a, set_a = votes[i]
        for source_b, set_b in votes[i + 1 :]:
            if not _compatible(source_a, set_a, source_b, set_b):
                return "Classifiers disagree"
    return None
```

Remove the now-unused `_HUMAN_REVIEW_SOURCES` constant's use in `_disagreement_reason` only if
nothing else references it — check first:

Run: `grep -n "_HUMAN_REVIEW_SOURCES" src/fledermap/services/review_flags.py`

`_has_human_review` (a separate function) still uses `_HUMAN_REVIEW_SOURCES` — keep the constant
and that function untouched; only `_disagreement_reason` itself changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_review_flags.py -v`
Expected: PASS (all pre-existing tests, with the one updated in Step 1, plus the new ones).

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check`
Expected: no errors — `review_flags.py` already has `from __future__ import annotations` at the
top, so the plain `tuple[Verdict] | tuple[Verdict, int | str]` union in the `_ClaimKey` alias
needs no quoting.

Run: `hatch fmt`
Expected: no changes needed (or auto-fixes applied cleanly).

- [ ] **Step 6: Run the full fast test suite**

Run: `hatch test -m "not db"`
Expected: PASS.

- [ ] **Step 7: Run the DB-marked tests**

Run with `dangerouslyDisableSandbox: true` (Docker is blocked by the command sandbox — see
CLAUDE.md's "Environment gotchas"): `hatch test -m db`
Expected: PASS, including `test_review_reasons_includes_disagreement_even_when_best_verdict_is_noise`
(EMT_GUANO NOISE vs. BATDETECT2 SPECIES — still flags: neither is excluded, and `{NOISE}` is
never equal to nor a superset of `{SPECIES: x}`).

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/services/review_flags.py tests/test_review_flags.py
git commit -m "$(cat <<'EOF'
feat: per-source pairwise disagreement voting in review_flags

Replaces the flat "distinct claim keys" check with per-source vote-sets
compared pairwise: a plain source's real NO_ID now counts as a vote
(disagreeing with a confident SPECIES claim elsewhere) unless the source
is_only_noise; two multi_support sources must match exactly to agree; a
plain source agrees with a multi_support source whenever its call is a
member of that source's set.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] Run the complete suite one more time end to end: `hatch test` (includes `-m db`, needs
  `dangerouslyDisableSandbox: true`).
- [ ] Run `hatch run types:check` once more over the whole tree.
- [ ] Confirm `docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md`'s
  §1–§5 are each covered: §2 by Task 1, §3 by Task 2, §4 by Task 3, §5 is an invariant relied on
  (not new code) and is exercised implicitly by every multi_support test in Tasks 2–3 above.
