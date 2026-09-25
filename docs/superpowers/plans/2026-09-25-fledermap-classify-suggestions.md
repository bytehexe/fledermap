# Classify-box likely-suggestions (part a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show 0–N clickable "likely" suggestion chips above the Classify box's Save button —
"Agree with classifiers" (from the automatic-only precedence winner) and "Noise" (from SNR /
nothing-found) — that add chips to the staged set on click, recomputing live as chips change.

**Architecture:** A new `automatic_best_identification` precedence walk in `current_best.py`
(manual sources excluded) feeds a new `services/classify_suggestions.py` that derives an
ordered, capped candidate list. The view embeds that list as JSON on `#classifier-box`
alongside the existing `data-taxon-search-index`. All "does this still apply given what's
staged" filtering is a pure client-side function in `classifier_logic.js`, re-run on every chip
mutation — no new network round-trip after the initial render/save.

**Tech Stack:** Flask/Jinja, SQLAlchemy, vanilla JS (`node:test` for pure-logic tests, no
frontend build step), pytest (`db`-marked tests need a real PostGIS testcontainer).

**Spec:** `docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-design.md`

## Global Constraints

- No frontend build step, no `package.json`, no npm dependency added to the project — any
  headless-Chrome verification tooling is throwaway, installed unsandboxed into the scratchpad
  directory only, never committed.
- A suggestion click only ever *adds* chips (via the existing `addChip()` path) — it never
  removes a chip, never auto-saves. Save remains an explicit, separate action.
- Two independent heuristics (Agree-with-classifiers, Noise) can both apply at once — never
  collapse them into a mutually-exclusive choice.
- Suggestion candidates are computed server-side once per render of `_classifier_box.html`
  (initial page load and every post-Save fragment re-render) — never per keystroke, never a new
  fetch on chip mutation.
- Server-side pool cap (8) ≥ client-side visible cap (3) — the client re-filters the embedded
  pool on every mutation so a satisfied suggestion can be replaced by a lower-priority one
  without a new request.
- Every icon-free plain `<button>` styling comes from the shared bare `button` rule
  (`docs/style-guide.md`) — no new global button variant beyond what's declared here.

## Review Focus

- An automatic SPECIES claim with an unresolved `taxon_id` (unmapped code) must not produce a
  broken or empty-taxon "Agree with classifiers" chip — verified in Task 2.
- Both "Agree with classifiers" and "Noise" can be true at once (a weak species match on an
  otherwise noisy recording) and must render as two independent, separately clickable buttons —
  verified in Task 2 (server) and Task 6 (live).
- SNR-threshold boundary: `snr_db == noise_snr_threshold_db` must NOT count as low (mirrors
  `services/noise.py`'s own strict `<` comparison exactly) — verified in Task 2.
- Clicking a multi-species "Agree with classifiers" suggestion when only some of its taxa are
  already staged must add only the missing ones, never a duplicate chip — verified in Task 5
  (JS) and Task 6 (live).
- Removing a chip that was the reason a suggestion was hidden must make that suggestion
  reappear (the server-pool-cap / client-visible-cap backfill) — verified in Task 5 (JS) and
  Task 6 (live).

---

## Task 1: `automatic_best_identification` in `current_best.py`

**Files:**
- Modify: `src/fledermap/services/current_best.py` (the `current_best_identification` function
  and its `_PRECEDENCE` constant, around lines 33–47 and 149–182)
- Test: `tests/test_current_best.py`

**Interfaces:**
- Produces: `automatic_best_identification(recording: Recording) -> CurrentIdentification | None`
  — same return shape as `current_best_identification`, but the precedence walk skips every
  `is_manual` source (`MANUAL`, `EMT_MANUAL`) entirely rather than merely falling through their
  `NO_ID`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_current_best.py` (uses the existing `_recording`/`_ident` helpers already
defined at the top of that file):

```python
from fledermap.services.current_best import automatic_best_identification


def test_automatic_best_ignores_manual_and_returns_the_automatic_species() -> None:
    r = _recording(
        _ident(IdSource.MANUAL, taxon_id=99),
        _ident(IdSource.EMT_GUANO, taxon_id=3),
    )

    best = automatic_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 3


def test_automatic_best_is_none_when_only_manual_claims_exist() -> None:
    r = _recording(_ident(IdSource.MANUAL, taxon_id=1))

    assert automatic_best_identification(r) is None


def test_automatic_best_is_none_when_no_claims_at_all() -> None:
    r = _recording()

    assert automatic_best_identification(r) is None


def test_automatic_best_resolves_to_noise_when_only_fledermap_noise_claims_noise() -> None:
    r = _recording(
        _ident(IdSource.FLEDERMAP_NOISE, taxon_id=None, verdict=Verdict.NOISE),
    )

    best = automatic_best_identification(r)

    assert best is not None
    assert best.verdict == Verdict.NOISE


def test_automatic_best_is_none_when_fledermap_noise_claims_no_id() -> None:
    """A non-manual source's pure NO_ID claim set folds to absent (`_live_claims`) --
    fledermap.noise's own NO_ID is no exception, so this must NOT resolve to NOISE or NO_ID,
    it must resolve to nothing at all."""
    r = _recording(
        _ident(IdSource.FLEDERMAP_NOISE, taxon_id=None, verdict=Verdict.NO_ID),
    )

    assert automatic_best_identification(r) is None


def test_automatic_best_supports_multi_species_from_batdetect2() -> None:
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = automatic_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_current_best.py -k automatic_best -v`
Expected: FAIL with `ImportError` / `AttributeError` — `automatic_best_identification` does not
exist yet.

- [ ] **Step 3: Implement `automatic_best_identification`**

In `src/fledermap/services/current_best.py`, replace the body of
`current_best_identification` with a shared private walk parameterized by the precedence
tuple, then add the new public function. The existing function's docstring is unchanged and
stays attached to `current_best_identification`; only its body moves into the new private
helper.

Replace:

```python
def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Walk sources in precedence order. ... (existing docstring, unchanged)"""
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

With:

```python
def _best_identification(
    recording: Recording,
    precedence: Sequence[IdSource],
) -> CurrentIdentification | None:
    for index, source in enumerate(precedence):
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
                precedence[index + 1 :],
            )
            if override is not None:
                return CurrentIdentification.from_matches(override)
        return CurrentIdentification.from_matches(matches)
    return None


_AUTOMATIC_PRECEDENCE: tuple[IdSource, ...] = tuple(
    source for source in _PRECEDENCE if not is_manual(source)
)


def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Walk sources in precedence order. ... (existing docstring, unchanged)"""
    return _best_identification(recording, _PRECEDENCE)


def automatic_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Like `current_best_identification`, but skips every `is_manual` source entirely
    (MANUAL, EMT_MANUAL) rather than merely falling through their NO_ID -- "what would the
    automatic classifiers alone resolve to, ignoring any human judgment already recorded."
    Used by `services/classify_suggestions.py`'s "Agree with classifiers"/"Noise" heuristics
    (design spec 2026-09-25-fledermap-classify-suggestions-design.md) to suggest a likely
    classification independent of whatever manual state is currently staged in the Classify
    box, including the "revert to what the classifiers said" case where a standing MANUAL
    claim already exists but disagrees.

    `automatic_best.verdict` can only ever be SPECIES, NOISE, or this function returns None --
    never a live NO_ID: `_live_claims`'s "all-NO_ID-from-a-non-manual-source is absent" rule
    means a pure automatic NO_ID (including `fledermap.noise`'s own) always folds to absent,
    never survives as a winning claim."""
    return _best_identification(recording, _AUTOMATIC_PRECEDENCE)
```

Note: `Sequence` is already imported at the top of this file
(`from collections.abc import Sequence`) for `CurrentIdentification.from_matches`'s own
signature — no new import needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_current_best.py -v`
Expected: PASS — all existing tests plus the six new ones.

- [ ] **Step 5: Type-check**

Run: `hatch run types:check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/current_best.py tests/test_current_best.py
git commit -m "feat: add automatic_best_identification, excluding manual sources from the precedence walk"
```

---

## Task 2: `services/classify_suggestions.py`

**Files:**
- Create: `src/fledermap/services/classify_suggestions.py`
- Test: `tests/test_classify_suggestions.py`

**Interfaces:**
- Consumes: `automatic_best_identification` (Task 1), `Recording`, `Verdict` (`domain.codes`).
- Produces:
  - `SuggestedTaxon` (frozen dataclass): `id: int`, `scientific_name: str`
  - `ClassifySuggestion` (frozen dataclass): `taxa: tuple[SuggestedTaxon, ...]`,
    `is_noise: bool`, `label: str`, `priority: float`
  - `classify_suggestions(recording: Recording, *, noise_snr_threshold_db: float, pool_cap: int = 8) -> list[ClassifySuggestion]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_classify_suggestions.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.classify_suggestions import SuggestedTaxon, classify_suggestions
from fledermap.store.models import Identification, Recording, Taxon


def _recording(*identifications: Identification, snr_db: float | None = None) -> Recording:
    r = Recording(
        audio_hash="a" * 64,
        path="x.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        snr_db=snr_db,
    )
    r.identifications = list(identifications)
    return r


def _taxon(taxon_id: int, name: str) -> Taxon:
    t = Taxon(rank="species", scientific_name=name)
    t.id = taxon_id
    return t


def _ident(
    source: IdSource,
    *,
    taxon: Taxon | None = None,
    verdict: Verdict = Verdict.SPECIES,
) -> Identification:
    ident = Identification(
        source=source,
        verdict=verdict,
        taxon_id=taxon.id if taxon is not None else None,
        first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    ident.taxon = taxon
    return ident


def test_no_claims_and_no_snr_suggests_noise_only() -> None:
    r = _recording()

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise
    assert suggestions[0].taxa == ()


def test_automatic_species_claim_suggests_agree_with_classifiers() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise is False
    assert suggestions[0].taxa == (
        SuggestedTaxon(id=1, scientific_name="Pipistrellus pipistrellus"),
    )
    assert "Pipistrellus pipistrellus" in suggestions[0].label


def test_low_snr_and_a_species_claim_both_suggest_at_once() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip), snr_db=1.0)

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 2
    assert suggestions[0].is_noise is False  # Agree with classifiers sorts first
    assert suggestions[1].is_noise is True


def test_snr_exactly_at_threshold_is_not_low() -> None:
    r = _recording(snr_db=2.0)

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    # Still suggested, but ONLY via the "nothing found" branch, not the SNR branch --
    # this test exists to pin the boundary; test_no_claims_and_no_snr_suggests_noise_only
    # already covers the "nothing found" branch producing the same single Noise result, so
    # a passing result here (rather than two Noise-shaped entries or a crash) confirms the
    # `<` comparison, not `<=`.
    assert len(suggestions) == 1
    assert suggestions[0].is_noise


def test_unmapped_automatic_species_claim_suggests_nothing() -> None:
    """An unresolved code has no taxon to turn into a chip -- and since automatic_best's
    verdict is SPECIES (not None/NOISE), the Noise heuristic doesn't fire either. No
    suggestion is better than a broken one here."""
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=None))

    assert classify_suggestions(r, noise_snr_threshold_db=2.0) == []


def test_manual_claim_does_not_suppress_the_automatic_suggestion() -> None:
    """classify_suggestions never looks at MANUAL state -- that filtering happens entirely
    client-side against whatever is currently staged."""
    pip = _taxon(1, "Pipistrellus pipistrellus")
    other = _taxon(2, "Myotis mystacinus")
    r = _recording(
        _ident(IdSource.MANUAL, taxon=other),
        _ident(IdSource.EMT_GUANO, taxon=pip),
    )

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert suggestions[0].taxa[0].id == 1


def test_multi_species_claim_produces_one_suggestion_with_both_taxa_sorted_by_name() -> None:
    a = _taxon(1, "Pipistrellus pipistrellus")
    b = _taxon(2, "Myotis mystacinus")
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon=a),
        _ident(IdSource.BATDETECT2, taxon=b),
    )

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert [t.scientific_name for t in suggestions[0].taxa] == [
        "Myotis mystacinus",
        "Pipistrellus pipistrellus",
    ]


def test_pool_cap_truncates_to_the_highest_priority_candidates() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip), snr_db=1.0)

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=1)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_classify_suggestions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.services.classify_suggestions'`.

- [ ] **Step 3: Implement `classify_suggestions.py`**

Create `src/fledermap/services/classify_suggestions.py`:

```python
"""Derives the Classify box's "likely" suggestion chips (design spec
2026-09-25-fledermap-classify-suggestions-design.md) -- two independent, explainable
heuristics for part (a):

- "Agree with classifiers": the automatic-only precedence winner
  (`current_best.automatic_best_identification`), when it's a SPECIES verdict.
- "Noise": either the recording's own SNR is below the configured threshold (the same
  comparison `services/noise.py` makes for `fledermap.noise`'s own claim), or no automatic
  classifier found anything at all.

Both can fire on the same recording -- they are independent signals, not a single decision;
see the design spec's "Two independent heuristics" section for why. This module never looks
at MANUAL state: whether a suggestion still "adds something" given what's currently staged in
the Classify box is decided entirely client-side (`classifier_logic.js`), so the same embedded
candidate list works whether the box is empty, mid-edit, or already holds a different manual
pick.

The returned list is ordered by `priority` descending and capped to `pool_cap` -- deliberately
looser than the UI's own visible cap, so the client can re-filter this same embedded list as
chips change without a new request (see the spec's "Candidate list" section)."""

from __future__ import annotations

from dataclasses import dataclass

from fledermap.domain.codes import Verdict
from fledermap.services.current_best import automatic_best_identification
from fledermap.store.models import Recording

_PRIORITY_AGREE_WITH_CLASSIFIERS = 2.0
_PRIORITY_NOISE = 1.0


@dataclass(frozen=True)
class SuggestedTaxon:
    id: int
    scientific_name: str


@dataclass(frozen=True)
class ClassifySuggestion:
    taxa: tuple[SuggestedTaxon, ...]  # empty means the Noise sentinel
    is_noise: bool
    label: str
    priority: float


def classify_suggestions(
    recording: Recording,
    *,
    noise_snr_threshold_db: float,
    pool_cap: int = 8,
) -> list[ClassifySuggestion]:
    automatic_best = automatic_best_identification(recording)
    candidates: list[ClassifySuggestion] = []

    if automatic_best is not None and automatic_best.verdict == Verdict.SPECIES:
        taxa = sorted(
            (
                SuggestedTaxon(id=claim.taxon_id, scientific_name=claim.taxon.scientific_name)
                for claim in automatic_best.claims
                if claim.taxon_id is not None and claim.taxon is not None
            ),
            key=lambda t: t.scientific_name,
        )
        if taxa:
            label = "Likely: " + ", ".join(t.scientific_name for t in taxa)
            candidates.append(
                ClassifySuggestion(
                    taxa=tuple(taxa),
                    is_noise=False,
                    label=label,
                    priority=_PRIORITY_AGREE_WITH_CLASSIFIERS,
                ),
            )

    low_snr = recording.snr_db is not None and recording.snr_db < noise_snr_threshold_db
    nothing_found = automatic_best is None or automatic_best.verdict == Verdict.NOISE
    if low_snr or nothing_found:
        candidates.append(
            ClassifySuggestion(taxa=(), is_noise=True, label="Likely: Noise", priority=_PRIORITY_NOISE),
        )

    candidates.sort(key=lambda c: c.priority, reverse=True)
    return candidates[:pool_cap]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_classify_suggestions.py -v`
Expected: PASS.

- [ ] **Step 5: Type-check**

Run: `hatch run types:check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/classify_suggestions.py tests/test_classify_suggestions.py
git commit -m "feat: derive Classify-box likely-suggestion candidates"
```

---

## Task 3: Wire `noise_snr_threshold_db` into `create_app`

**Files:**
- Modify: `src/fledermap/web/app.py` (`create_app`, around lines 51–121)
- Modify: `src/fledermap/cli/main.py` (the `serve` command's `create_app(...)` call, around
  lines 433–445)
- Test: `tests/test_web_app.py`

**Interfaces:**
- Produces: `app.config["NOISE_SNR_THRESHOLD_DB"]` (a `float`), for Task 4 to read.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web_app.py`:

```python
def test_create_app_stores_noise_snr_threshold_on_config(
    tmp_path: Path, engine: Engine
) -> None:
    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        noise_snr_threshold_db=3.5,
    )
    assert app.config["NOISE_SNR_THRESHOLD_DB"] == 3.5


def test_create_app_defaults_noise_snr_threshold(tmp_path: Path, engine: Engine) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    assert app.config["NOISE_SNR_THRESHOLD_DB"] == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_web_app.py -k noise_snr_threshold -v`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'noise_snr_threshold_db'`.

- [ ] **Step 3: Implement**

In `src/fledermap/web/app.py`, add a new parameter to `create_app`'s signature, right after
`battybirdnet_annotation_display_threshold: float = 0.20,`:

```python
    noise_snr_threshold_db: float = 2.0,
```

Add a paragraph to `create_app`'s docstring, after the existing
`batdetect2_annotation_display_threshold`/`battybirdnet_annotation_display_threshold`
paragraph:

```
    `noise_snr_threshold_db` is `Config.noise_snr_threshold_db` -- exposed on
    `app.config["NOISE_SNR_THRESHOLD_DB"]` so the recording-details page's Classify box can
    suggest "Noise" when a recording's own `snr_db` falls below it, using the same comparison
    `services/noise.py` makes for `fledermap.noise`'s own claim (design spec
    2026-09-25-fledermap-classify-suggestions-design.md). Default matches `Config`'s own
    default.
```

In the function body, alongside the other `app.config[...]` assignments (after
`app.config["CLASSIFIER_DISPLAY_THRESHOLDS"] = {...}`):

```python
    app.config["NOISE_SNR_THRESHOLD_DB"] = noise_snr_threshold_db
```

In `src/fledermap/cli/main.py`, add one line to the `create_app(...)` call in `serve`:

```python
    app = create_app(
        engine,
        config.static_root,
        config.media_root,
        config.archive_roots,
        config.batdetect2_enabled,
        config.battybirdnet_enabled,
        config.combine_emt_display,
        config.batdetect2_threshold,
        config.battybirdnet_threshold,
        config.batdetect2_annotation_display_threshold,
        config.battybirdnet_annotation_display_threshold,
        config.noise_snr_threshold_db,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_web_app.py -v`
Expected: PASS.

- [ ] **Step 5: Type-check**

Run: `hatch run types:check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/app.py src/fledermap/cli/main.py tests/test_web_app.py
git commit -m "feat: expose noise_snr_threshold_db on the Flask app config"
```

---

## Task 4: View wiring + template markup + CSS

**Files:**
- Modify: `src/fledermap/web/views/recording_detail.py` (add a helper, extend
  `recording_details_page`'s render call)
- Modify: `src/fledermap/web/views/map.py` (extend `post_manual_classification`'s
  `_classifier_box.html` render call, and its import line)
- Modify: `src/fledermap/web/templates/_classifier_box.html`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `classify_suggestions`, `ClassifySuggestion` (Task 2);
  `app.config["NOISE_SNR_THRESHOLD_DB"]` (Task 3).
- Produces: a `suggestions` template variable (list of JSON-serializable dicts) available
  everywhere `_classifier_box.html` is rendered; `#classifier-box`'s
  `data-classify-suggestions` attribute (JSON array); `#classifier-likely-row` div in the
  rendered markup.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_recording_detail_view.py`:

```python
def test_classifier_box_embeds_likely_suggestions(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="q" * 64,
            path="q.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=recording.recorded_at,
            ),
        )
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'q' * 64}")
    html = response.get_data(as_text=True)

    # Same pattern as the existing test_taxon_search_index_attribute_is_valid_json:
    # tojson escapes `'` but not `"`, so the attribute must be single-quoted, and the
    # right way to assert on its content is to parse it back out as JSON, not to guess
    # at HTML-escaping by eye.
    match = re.search(r"data-classify-suggestions='([^']*)'", html)
    assert match is not None, (
        "expected a single-quoted data-classify-suggestions attribute"
    )
    suggestions = json.loads(match.group(1))
    assert len(suggestions) == 1
    assert suggestions[0]["is_noise"] is False
    assert suggestions[0]["taxa"] == [
        {"id": taxon_id, "scientific_name": "Pipistrellus pipistrellus"}
    ]
    assert 'id="classifier-likely-row"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_recording_detail_view.py -k likely_suggestions -v`
Expected: FAIL — `data-classify-suggestions` not present in the rendered HTML.

- [ ] **Step 3: Implement the view helper**

In `src/fledermap/web/views/recording_detail.py`, add these imports:

```python
from fledermap.services.classify_suggestions import ClassifySuggestion, classify_suggestions
```

Add a new function, near `_taxon_search_index` (both are small serialization helpers used by
both this module and `map.py`):

```python
def _serialize_classify_suggestion(s: ClassifySuggestion) -> dict[str, object]:
    return {
        "taxa": [{"id": t.id, "scientific_name": t.scientific_name} for t in s.taxa],
        "is_noise": s.is_noise,
        "label": s.label,
    }


def _classify_suggestions_data(recording: Recording) -> list[dict[str, object]]:
    """Shared by the details page's initial render and the manual-classification save's
    `_classifier_box.html` fragment re-render (`web/views/map.py`'s
    `post_manual_classification`) -- both must embed the same suggestion pool."""
    suggestions = classify_suggestions(
        recording,
        noise_snr_threshold_db=flask.current_app.config["NOISE_SNR_THRESHOLD_DB"],
    )
    return [_serialize_classify_suggestion(s) for s in suggestions]
```

In `recording_details_page`, after the existing
`taxon_search_index = _taxon_search_index(session)` line, add:

```python
        suggestions = _classify_suggestions_data(recording)
```

And add `suggestions=suggestions,` to the `flask.render_template("recording_details.html", ...)`
call (anywhere among the existing keyword arguments — e.g. right after `taxon_search_index=taxon_search_index,`).

- [ ] **Step 4: Wire the second render site in `map.py`**

In `src/fledermap/web/views/map.py`, change the import at the bottom:

```python
from fledermap.web.views.recording_detail import (
    _classify_suggestions_data,
    _current_classification,
    _taxon_search_index,
)
```

In `post_manual_classification`, in the `fragments = [...]` list, add `suggestions=` to the
existing `_classifier_box.html` render call:

```python
            flask.render_template(
                "_classifier_box.html",
                recording=recording,
                manual_verdict=manual_verdict,
                current_taxa=current_taxa,
                taxon_search_index=_taxon_search_index(session),
                suggestions=_classify_suggestions_data(recording),
            ),
```

- [ ] **Step 5: Add the template markup**

In `src/fledermap/web/templates/_classifier_box.html`, add `data-classify-suggestions` to the
root div, alongside the existing `data-taxon-search-index`:

```html
<div
  id="classifier-box"
  class="classifier-box"
  data-audio-hash="{{ recording.audio_hash }}"
  data-taxon-search-index='{{ taxon_search_index | tojson }}'
  data-classify-suggestions='{{ suggestions | tojson }}'
>
```

Add the row div between `.classifier-hint` and `.classifier-error`:

```html
  <p class="classifier-hint">
    Can't pin the exact species? Search "HiF", "LoF", or "Hilo" for a frequency-class group,
    or "No ID"/"Noise" if there's nothing to identify.
  </p>
  <div class="classifier-likely-row" id="classifier-likely-row"></div>
  <p class="classifier-error" id="classifier-error" hidden></p>
```

Add one sentence to the template's top comment block (after the existing paragraph ending
"...not just this client-side check."):

```
   The Classify box also shows 0-N "likely" suggestion buttons (#classifier-likely-row,
   populated by classifier_box.js from data-classify-suggestions) above Save -- see design
   spec 2026-09-25-fledermap-classify-suggestions-design.md. Clicking one adds its chip(s)
   via the same addChip() path a search result uses; it never removes a chip and never saves.
```

- [ ] **Step 6: Add CSS**

In `src/fledermap/web/static/app.css`, after the `.classifier-hint` rule (before
`.classifier-actions`):

```css
.classifier-likely-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.4rem 0; }
/* Dashed border distinguishes an unapplied suggestion (an action to take) from an
   already-staged .classifier-chip (state) -- both otherwise share this app's plain button
   look. */
.classifier-likely-choice { font-size: 0.8rem; border-style: dashed; }
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `hatch test tests/test_recording_detail_view.py -k likely_suggestions -v`
Expected: PASS.

- [ ] **Step 8: Run the full non-db suite and mypy**

Run: `hatch test -m "not db"` then `hatch run types:check`
Expected: both pass.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/views/recording_detail.py src/fledermap/web/views/map.py \
  src/fledermap/web/templates/_classifier_box.html src/fledermap/web/static/app.css \
  tests/test_recording_detail_view.py
git commit -m "feat: embed Classify-box likely-suggestion data in the rendered page"
```

---

## Task 5: Client-side rendering and interaction

**Files:**
- Modify: `src/fledermap/web/static/classifier_logic.js` (pure logic)
- Modify: `src/fledermap/web/static/classifier_box.js` (DOM wiring)
- Test: `tests/js/classifier_logic.test.js`

**Interfaces:**
- Consumes: `#classifier-box`'s `data-classify-suggestions` (JSON array of
  `{taxa: [{id, scientific_name}], is_noise, label}`), the existing `currentChips()`/`addChip()`
  from `classifier_box.js`.
- Produces (exported from `classifier_logic.js`): `CLASSIFY_LIKELY_VISIBLE_CAP` (number),
  `likelyChoiceAddsSomething(suggestion, stagedChips)` (boolean),
  `applicableLikelyChoices(suggestions, stagedChips, visibleCap)` (array, filtered + capped,
  order preserved).

- [ ] **Step 1: Write the failing tests**

Add to `tests/js/classifier_logic.test.js`:

```javascript
const {
  matchesQuery,
  deriveSavePayload,
  SENTINEL_ENTRIES,
  likelyChoiceAddsSomething,
  applicableLikelyChoices,
  CLASSIFY_LIKELY_VISIBLE_CAP,
} = require("../../src/fledermap/web/static/classifier_logic.js");

const agreeSuggestion = {
  taxa: [{ id: 1, scientific_name: "Pipistrellus pipistrellus" }],
  is_noise: false,
  label: "Likely: Pipistrellus pipistrellus",
};

const multiSuggestion = {
  taxa: [
    { id: 1, scientific_name: "Pipistrellus pipistrellus" },
    { id: 2, scientific_name: "Myotis mystacinus" },
  ],
  is_noise: false,
  label: "Likely: Myotis mystacinus, Pipistrellus pipistrellus",
};

const noiseSuggestion = { taxa: [], is_noise: true, label: "Likely: Noise" };

test("likelyChoiceAddsSomething is true for a species suggestion with nothing staged", () => {
  assert.ok(likelyChoiceAddsSomething(agreeSuggestion, []));
});

test("likelyChoiceAddsSomething is false once the exact taxon is already staged", () => {
  assert.equal(
    likelyChoiceAddsSomething(agreeSuggestion, [{ kind: "taxon", id: "1" }]),
    false,
  );
});

test("likelyChoiceAddsSomething is true for a multi-species suggestion partially staged", () => {
  assert.ok(likelyChoiceAddsSomething(multiSuggestion, [{ kind: "taxon", id: "1" }]));
});

test("likelyChoiceAddsSomething is false for a multi-species suggestion fully staged", () => {
  assert.equal(
    likelyChoiceAddsSomething(multiSuggestion, [
      { kind: "taxon", id: "1" },
      { kind: "taxon", id: "2" },
    ]),
    false,
  );
});

test("likelyChoiceAddsSomething is true for Noise with nothing staged", () => {
  assert.ok(likelyChoiceAddsSomething(noiseSuggestion, []));
});

test("likelyChoiceAddsSomething is false once Noise is already staged", () => {
  assert.equal(
    likelyChoiceAddsSomething(noiseSuggestion, [{ kind: "sentinel", verdict: "noise" }]),
    false,
  );
});

test("applicableLikelyChoices drops satisfied suggestions and keeps order", () => {
  const result = applicableLikelyChoices(
    [agreeSuggestion, noiseSuggestion],
    [{ kind: "taxon", id: "1" }],
    CLASSIFY_LIKELY_VISIBLE_CAP,
  );
  assert.deepEqual(result, [noiseSuggestion]);
});

test("applicableLikelyChoices caps to visibleCap", () => {
  const many = [agreeSuggestion, noiseSuggestion, multiSuggestion];
  const result = applicableLikelyChoices(many, [], 2);
  assert.equal(result.length, 2);
  assert.deepEqual(result, [agreeSuggestion, noiseSuggestion]);
});

test("applicableLikelyChoices lets a satisfied suggestion's neighbor reappear once staged chips change", () => {
  const pool = [agreeSuggestion, noiseSuggestion];
  const beforeRemoval = applicableLikelyChoices(
    pool,
    [{ kind: "taxon", id: "1" }, { kind: "sentinel", verdict: "noise" }],
    CLASSIFY_LIKELY_VISIBLE_CAP,
  );
  assert.deepEqual(beforeRemoval, []);

  const afterRemovingNoise = applicableLikelyChoices(
    pool,
    [{ kind: "taxon", id: "1" }],
    CLASSIFY_LIKELY_VISIBLE_CAP,
  );
  assert.deepEqual(afterRemovingNoise, [noiseSuggestion]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/js/classifier_logic.test.js`
Expected: FAIL — the new exports don't exist yet.

- [ ] **Step 3: Implement the pure logic**

In `src/fledermap/web/static/classifier_logic.js`, add (before the final
`if (typeof module !== "undefined" ...)` export block):

```javascript
// The Classify box's "likely" suggestion row (design spec
// 2026-09-25-fledermap-classify-suggestions-design.md) -- pure filtering over the
// server-embedded candidate pool (#classifier-box's data-classify-suggestions), re-run on
// every chip mutation by classifier_box.js's renderLikelyChoices. CLASSIFY_LIKELY_VISIBLE_CAP
// is deliberately smaller than the server's own pool cap so a satisfied suggestion's
// lower-priority neighbor can surface without a new request.
const CLASSIFY_LIKELY_VISIBLE_CAP = 3;

function likelyChoiceAddsSomething(suggestion, stagedChips) {
  if (suggestion.is_noise) {
    return !stagedChips.some((c) => c.kind === "sentinel" && c.verdict === "noise");
  }
  const stagedTaxonIds = new Set(
    stagedChips.filter((c) => c.kind === "taxon").map((c) => c.id),
  );
  return suggestion.taxa.some((t) => !stagedTaxonIds.has(String(t.id)));
}

function applicableLikelyChoices(suggestions, stagedChips, visibleCap) {
  return suggestions
    .filter((s) => likelyChoiceAddsSomething(s, stagedChips))
    .slice(0, visibleCap);
}
```

Update the export block to include the three new names:

```javascript
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    matchesQuery,
    deriveSavePayload,
    SENTINEL_ENTRIES,
    CLASSIFY_LIKELY_VISIBLE_CAP,
    likelyChoiceAddsSomething,
    applicableLikelyChoices,
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/js/classifier_logic.test.js`
Expected: PASS.

- [ ] **Step 5: Wire the DOM in `classifier_box.js`**

In `src/fledermap/web/static/classifier_box.js`, inside `initClassifierBox(box)`, after the
existing `const searchIndex = ...` line, add:

```javascript
  const suggestions = JSON.parse(box.dataset.classifySuggestions);
```

Add two new nested functions, near `addChip` (after it, since `addLikelyChoice` calls it):

```javascript
  function renderLikelyChoices() {
    const rowEl = box.querySelector("#classifier-likely-row");
    if (!rowEl) return;
    rowEl.innerHTML = "";
    const applicable = applicableLikelyChoices(
      suggestions,
      currentChips(),
      CLASSIFY_LIKELY_VISIBLE_CAP,
    );
    for (const suggestion of applicable) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "classifier-likely-choice";
      button.textContent = suggestion.label;
      button.addEventListener("click", () => {
        addLikelyChoice(suggestion);
        renderLikelyChoices();
      });
      rowEl.appendChild(button);
    }
  }

  function addLikelyChoice(suggestion) {
    if (suggestion.is_noise) {
      const alreadyStaged = currentChips().some(
        (c) => c.kind === "sentinel" && c.verdict === "noise",
      );
      if (!alreadyStaged) addChip({ sentinelVerdict: "noise", scientific_name: "Noise" });
      return;
    }
    const stagedIds = new Set(
      currentChips().filter((c) => c.kind === "taxon").map((c) => c.id),
    );
    for (const taxon of suggestion.taxa) {
      if (!stagedIds.has(String(taxon.id))) addChip(taxon);
    }
  }
```

Call `renderLikelyChoices()` at the end of `addChip(entry)`, right after
`classifierBoxDirty = true;`:

```javascript
    tagsEl.appendChild(chip);
    wireRemoveButton(chip);
    classifierBoxDirty = true;
    renderLikelyChoices();
  }
```

Call it inside `wireRemoveButton`'s click listener too, right after `classifierBoxDirty = true;`:

```javascript
  function wireRemoveButton(chipEl) {
    chipEl.querySelector(".classifier-chip-remove").addEventListener("click", () => {
      chipEl.remove();
      classifierBoxDirty = true;
      renderLikelyChoices();
    });
  }
```

Finally, call it once for the initial render, at the end of `initClassifierBox` (after the
existing `saveButton.addEventListener("click", save);` line):

```javascript
  saveButton.addEventListener("click", save);
  renderLikelyChoices();
}
```

- [ ] **Step 6: Run the full JS suite**

Run: `node --test tests/js/`
Expected: PASS, no regressions in other files.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/static/classifier_logic.js src/fledermap/web/static/classifier_box.js \
  tests/js/classifier_logic.test.js
git commit -m "feat: render and wire the Classify-box likely-suggestion buttons"
```

---

## Task 6: Live verification

No new automated test in this task — this is the mandatory headless-Chrome check this
project's CLAUDE.md requires for any JS change ("A file with a top-level
document.addEventListener..." section / "mandatory for any task that adds or changes JS").

**Files:** none modified. Verification only.

- [ ] **Step 1: Start a throwaway dev server**

The controlling session (not a dispatched subagent) runs this directly, unsandboxed —
`localhost:5432` DB access needs it:

```bash
cd /home/janna/projekte/bats
FLEDERMAP_PORT=5099 hatch run fledermap serve
```

Leave it running; it reads the same `~/.config/fledermap/config.toml` the production instance
uses (real `bats_db`, read-only for this verification — the Classify box's Save is exercised
deliberately below, so pick a recording whose MANUAL state you don't mind changing, or a
throwaway audio_hash from a recent ingest, not one you care about preserving).

- [ ] **Step 2: Install puppeteer-core into the scratchpad (throwaway, not a project dependency)**

```bash
cd /tmp/claude-*/*/scratchpad 2>/dev/null || cd "$TMPDIR"
npm install puppeteer-core --no-save
```

- [ ] **Step 3: Find a recording whose automatic classifiers found a species AND whose SNR is
  low** (to exercise "both suggestions show at once" — the Review Focus item most likely to be
  wrong). Query `http://localhost:5099/api/recordings.geojson` for a candidate `audio_hash`, or
  pick any recording and note its state from the page itself.

- [ ] **Step 4: Write and run a throwaway verification script**

Save to the scratchpad as `verify_likely_suggestions.js`, adjusting `AUDIO_HASH`:

```javascript
const puppeteer = require("puppeteer-core");

const AUDIO_HASH = "REPLACE_ME";

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.goto(`http://localhost:5099/recordings/${AUDIO_HASH}`, {
    waitUntil: "networkidle0",
  });

  const buttons = await page.$$eval("#classifier-likely-row button", (els) =>
    els.map((el) => el.textContent),
  );
  console.log("Suggestion buttons on load:", buttons);

  if (buttons.length === 0) {
    console.log("No suggestions on this recording -- pick a different audio_hash.");
    await browser.close();
    return;
  }

  // Click the first suggestion; confirm a chip was added and the button either
  // disappeared (fully satisfied) or the row otherwise changed.
  await page.click("#classifier-likely-row button");
  const chipsAfterClick = await page.$$eval("#classifier-tags .classifier-chip", (els) =>
    els.map((el) => el.textContent.trim()),
  );
  console.log("Chips after clicking first suggestion:", chipsAfterClick);

  const buttonsAfterClick = await page.$$eval("#classifier-likely-row button", (els) =>
    els.map((el) => el.textContent),
  );
  console.log("Suggestion buttons after click:", buttonsAfterClick);

  // Remove the chip just added; confirm the suggestion reappears (the backfill case).
  await page.click("#classifier-tags .classifier-chip .classifier-chip-remove");
  const buttonsAfterRemoval = await page.$$eval("#classifier-likely-row button", (els) =>
    els.map((el) => el.textContent),
  );
  console.log("Suggestion buttons after removing the chip:", buttonsAfterRemoval);

  await browser.close();
})();
```

```bash
node verify_likely_suggestions.js
```

- [ ] **Step 5: Confirm expected output by hand**

Check the printed output against these expectations (per this plan's Review Focus):
- If the chosen recording has both a weak automatic species match and low SNR: "Suggestion
  buttons on load" lists two entries, one starting `Likely: ` with a species name and one
  reading `Likely: Noise`.
- After clicking the first button: `chipsAfterClick` includes the clicked suggestion's
  species/Noise; `buttonsAfterClick` no longer lists that same suggestion (it's now satisfied),
  but still lists the other one if it hadn't been satisfied too.
- After removing the chip: `buttonsAfterRemoval` lists the suggestion again — confirming the
  server-pool/client-visible-cap backfill from Task 2/5 actually works live, not just in the
  `node:test` simulation.

If a multi-species suggestion is available on some recording, repeat Step 4's click against
one where only one of its taxa is already staged, and confirm only the missing taxon's chip
gets added (not a duplicate of the already-staged one).

- [ ] **Step 6: Stop the throwaway dev server**

```bash
pgrep -f "FLEDERMAP_PORT=5099" | xargs -r kill
```

Confirm the real systemd-managed instance (port 5001) was never touched.

- [ ] **Step 7: Report findings**

No commit for this task (nothing changed). If verification surfaces a real bug, fix it as a
new commit against the task that introduced it (Task 4 or 5) and re-run this task's checklist
from Step 4.
