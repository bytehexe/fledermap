# Fledermap — classify-box likely-suggestions design (part a)

Status: proposed. Scope: `services/current_best.py` (new `automatic_best_identification`),
a new suggestion-derivation module, `_classifier_box.html`, `classifier_box.js`/
`classifier_logic.js`, `app.css`. Trigger: speeding up the manual-classification workflow by
surfacing likely choices above the Save button, inspired by Google Health-style "likely
activity" suggestion chips — several plausible options offered at once, the user picks what
fits (or ignores them and searches/types as today).

This is **part (a)** of a two-part idea: simple, explainable heuristics only. Part (b) —
mining historical MANUAL-vs-automatic-classifier corrections for implication rules ("if A and
B, the user usually picks C"), plus using sub-threshold `ClassifierDetection` candidates — is
a distinct follow-on design, deferred until there's more MANUAL-classification history to mine
and the part (a) UI has been used for a while. Part (a)'s data shapes are deliberately built so
part (b) plugs in as an additional candidate source rather than requiring a rework — see §4.

## 1. Two independent heuristics

Both are evaluated independently and can both fire on the same recording — they are not
mutually exclusive branches of one decision. This project is not judging which is "more
correct"; it's offering plausible starting points to save typing.

**A. Agree with classifiers.** `automatic_best_identification(recording)` — a new function
in `current_best.py`, identical to the existing `current_best_identification`'s precedence
walk except it starts at `EMT_GUANO`, entirely skipping `is_manual` sources (`MANUAL`,
`EMT_MANUAL`). Returns the same `CurrentIdentification | None` shape, so multi-species results
and the existing containment-override logic apply unchanged.

Shown only when `automatic_best.verdict == SPECIES` **and** its `taxon_ids` is not already a
subset of the currently-staged taxon ids — i.e. only when accepting it would actually add at
least one taxon that isn't already staged. If accepting it would add nothing, it's not shown
(this covers "already staged and matching" as a special case of "adds nothing" — no separate
equality check needed).

**B. Noise.** Shown when either:
- `recording.snr_db is not None and recording.snr_db < noise_snr_threshold_db` (the same
  comparison `services/noise.py` already makes for `fledermap.noise`'s own claim), or
- `automatic_best_identification(recording)` is `None` or has `verdict == NOISE` (nothing else
  claimed anything at all — covers both "SNR is fine but nothing was found" and "SNR was never
  computed and nothing was found," per 2026-09-25 discussion: absence of any classifier finding
  is itself weak evidence for noise, regardless of whether SNR evidence exists).

...unless the Noise sentinel is already the staged chip (nothing to add).

Note on why `automatic_best`'s own verdict can only ever be `SPECIES`, `NOISE`, or absent
(never a live `NO_ID`): `_live_claims`'s existing rule folds an all-NO_ID claim set from any
non-manual source into "absent" (falls through). Since `automatic_best_identification` walks
only non-manual sources, a live `NO_ID` result can never actually occur — it always resolves to
`SPECIES`, `NOISE` (from `fledermap.noise` when nothing upstream claimed anything and SNR is
low), or `None`.

## 2. Candidate list — generalized for part (b)

Rather than two hardcoded UI slots, the server produces an ordered list of candidates:

```python
@dataclass(frozen=True)
class ClassifySuggestion:
    taxon_ids: frozenset[int]   # empty means "the Noise sentinel"
    is_noise: bool
    label: str                 # e.g. "Likely: Pipistrellus pipistrellus"
    priority: float             # ordering key, see below
```

Ordering: A and B from this feature are deterministic yes/no signals, not probability-ranked,
so they always sort first, in that fixed order (A before B) when both fire. Any part-(b)
candidates append after them, sorted by their own likelihood score descending — `priority`
is a plain sort key, not a displayed probability; part (a)'s two candidates just use fixed
sentinel priorities high enough to always sort first.

**Two separate caps**, so suggestions can "re-appear" as staged chips change without a new
network round-trip:

- **Server-side pool cap** (looser, e.g. top 8): computed once per render of
  `_classifier_box.html` (initial load and every post-Save re-render), embedded as a JSON data
  attribute alongside the existing `data-taxon-search-index`.
- **Client-side visible cap** (stricter, e.g. top 3): on every chip mutation (search-result
  click, suggestion click, or chip removal), the client re-filters the *embedded* pool —
  dropping any candidate that would add nothing given the currently-staged chips — and renders
  only the top N of what's left, in the same priority order. A candidate that was hidden
  because a higher-priority one already covered it can reappear once that higher one is
  satisfied or removed; a candidate that isn't in the embedded pool at all never appears
  (no backfill beyond the server-sent pool without a new render).

Exact cap numbers are an implementation-plan detail, not a design commitment here.

For part (a) alone, the pool never exceeds 2 candidates, so the two caps are inert — this
structure exists purely so part (b) doesn't require a schema or client-logic change, only an
additional server-side candidate-generating function feeding the same list.

## 3. UI / interaction

New markup in `_classifier_box.html`, between `.classifier-hint` and the error/actions row: a
`.classifier-suggestions-row` holding 0–N pill-style `<button>`s. These reuse the bare
`button` styling from the style guide (not `.classifier-chip`'s look) so they read as *actions
to take*, distinct from already-staged chips.

Clicking a suggestion button calls the existing `addChip()` path (same as clicking a search
result) for whichever of its taxa aren't already staged, or adds the Noise sentinel chip. It
never removes anything and never auto-saves — Save still requires an explicit click, per the
existing "a form that changes stored data needs an explicit Save" rule. After every chip
mutation, the suggestion row recomputes and re-renders per §2's client-side filter/cap.

Typing into the search field does not affect the suggestion row — suggestions react to staged
chips, not to search text.

## 4. Forward compatibility for part (b)

When part (b) lands, it becomes one more server-side function producing `ClassifySuggestion`
entries (backed by a precomputed/cached table of historical patterns, not a live query per
render — mining is not something to redo on every page load) appended to the same list before
the server-side pool cap is applied. No change to the `ClassifySuggestion` shape, the
priority-ordering rule, the two-cap mechanism, or the client-side filter/recompute logic is
expected.

## 5. Testing

- **Python**: unit tests for `automatic_best_identification` (manual-present, manual-absent,
  multi-species, noise-only, nothing-at-all — mirroring `current_best_identification`'s
  existing test shapes) and for the candidate-generation function (A and B independently,
  both at once, neither, the "adds nothing" suppression, `snr_db` unknown vs. known-high vs.
  known-low).
- **JS**: the candidate-filtering/recompute logic is pure (candidate list + staged chips →
  which to show, capped) and splits into `classifier_logic.js` alongside `deriveSavePayload`,
  covered by `node:test`.
- **Live verification** (mandatory for any JS change, per CLAUDE.md): headless Chrome —
  clicking a suggestion adds the right chip(s) and the button disappears once satisfied; A and
  B can show simultaneously; a hidden candidate reappears after removing the chip that
  satisfied it; Save still round-trips correctly through a full suggestion-assisted flow.
