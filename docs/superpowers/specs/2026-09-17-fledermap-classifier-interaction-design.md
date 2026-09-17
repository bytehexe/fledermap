# Fledermap — classifier interaction design

Status: proposed. Scope: `domain/codes.py` (new classifier property model), `services/
current_best.py` (`current_best_identification`'s precedence walk), and `services/
review_flags.py` (`_disagreement_reason`). Obsidian backlog item "Refine classifier logic" is
the trigger.

## 1. Problem

`current_best_identification` and `_disagreement_reason` each hardcode per-`IdSource` special
cases (`source != IdSource.MANUAL`, `_HUMAN_REVIEW_SOURCES`) rather than deriving behavior from
what kind of classifier a source actually is. This has produced four concrete gaps:

1. **Multi-species results are effectively MANUAL-only in practice**, even though
   `services/classifier_detections.py`'s `materialize_claims` already produces more than one
   `Identification` row per recording for `BATDETECT2`/`BATTYBIRDNET` (one per distinct species
   clearing `threshold`) — `current_best.py`'s own docstring claims "every other source has at
   most one live row per recording," which is no longer true for these two sources.
2. **A higher-precedence single-species source overrides a lower-precedence multi-species result
   outright, even when it doesn't actually disagree with it** — e.g. `EMT_GUANO` calling PIPPIP
   discards a `BATDETECT2` result of `{PIPPIP, MYODAU}` entirely, losing the MYODAU information,
   even though PIPPIP is consistent with it.
3. **A classifier's `NO_ID` never counts as a vote in the disagreement check**
   (`review_flags.py`'s `_disagreement_reason`), so a real classifier confidently finding nothing
   while another confidently finds a species never flags for review — except this *should* be
   uninteresting specifically for `FLEDERMAP_NOISE` (a coarse pre-filter expected to say "nothing
   here" alongside a real classifier's confident species call), not for classifiers generally.
4. **The disagreement check votes per-claim, not per-source**, so a multi-species result's own
   claims can spuriously count as several disagreeing votes against each other or against a
   consistent higher-precedence single call, once multi-species results exist outside MANUAL.

The fix in every case is a declarative property model on `IdSource`, so a future classifier
participates correctly by declaring its properties rather than needing new per-source code in
either `current_best.py` or `review_flags.py`.

## 2. Property model (`domain/codes.py`)

Three boolean properties, looked up via a small mapping next to `IdSource` (same idiom as the
existing `_SENTINEL_VERDICTS` table — `IdSource` itself stays a plain `StrEnum`):

- **`is_manual`**: `{MANUAL, EMT_MANUAL}`. A human's deliberate judgment — its own `NO_ID` is a
  considered "I checked and there's nothing here," not an admission of uncertainty, so it stops
  the precedence walk outright ("strong NoID") instead of falling through like an automatic
  classifier's `NO_ID` does. `EMT_MANUAL` moves from today's "weak NoID" behavior to match
  `MANUAL` — this is a deliberate behavior change (see §3), not just a naming exercise. An
  `is_manual` source is also excluded entirely from `_disagreement_reason`'s voting (already true
  today via `_HUMAN_REVIEW_SOURCES`, and already moot in practice since `_has_human_review` gates
  the whole `review_reasons` call before it — kept as an explicit property because the disagreement
  function is also called on its own).
- **`only_noise`**: `{FLEDERMAP_NOISE}`. A source whose only possible verdicts are `NOISE` and
  `NO_ID` (it can never assert a species). Its `NO_ID` is excluded from disagreement voting — an
  admission from a coarse pre-filter that it can't tell either way is not informative alongside a
  real classifier's confident species call. Its `NOISE` claim is **not** excluded — NOISE vs.
  SPECIES from another source keeps flagging exactly as it does today (this was *not* the gap the
  backlog item was about; only the `NO_ID` case is new).
- **`multi_support`**: `{MANUAL, BATDETECT2, BATTYBIRDNET}`. A source whose claims for one
  recording may legitimately name more than one taxon at once, rather than every extra claim past
  the first being either a competing alternative or an error.

`_PRECEDENCE`'s order in `current_best.py` is unchanged — this spec is entirely about how sources
already known to the walk interact, not about reordering them.

## 3. `current_best_identification` walk (`current_best.py`)

### 3.1 Generalized weak/strong NoID

The existing skip — "a source's claims are skipped when they are ALL automatic `NO_ID`" —
generalizes from `source != IdSource.MANUAL` to `not is_manual(source)`. Net effect: `EMT_MANUAL`'s
own `NO_ID` now stops the walk (wins outright) instead of falling through to `EMT_GUANO`/etc.,
matching `MANUAL`'s existing behavior.

### 3.2 Multi-support containment override

When the first source with live claims (per §3.1's skip) is a **plain** source — not `is_manual`,
not `multi_support` — and its verdict is `SPECIES`, the walk now looks further down the precedence
order before accepting it as the final winner:

1. Continue past the winner using the same live-claims skip logic (a `multi_support`-blind
   automatic source with an all-`NO_ID` claim set is transparently skipped here exactly as it
   would be in the main walk — this is the "there could be a weak NoID in between" case).
2. Stop at the **nearest** source, if any, that is `multi_support` and has live claims. Do not
   look further than this one, even if it fails the checks below — the highest precedence source
   that actually disagrees is the one that should win, and stopping there means a real
   disagreement still gets flagged (§4) instead of being silently resolved by digging for an
   even-lower-precedence match.
3. That source must be **effectively multi-support** for this recording (§5: every one of its
   live claims has verdict `SPECIES` — a mixed `SPECIES`+`NOISE` set means the source is
   contradicting itself, not offering a coherent multi-species answer to defer to) and must have
   **more than one** live claim (a singleton claim set gains nothing by overriding the winner —
   see the worked example below for why this matters in practice, not just in theory). Either
   failing sends this straight to step 5 (the original winner stands).
4. If that source's claim set contains the winner's `taxon_id`: the winner is **not** a real
   disagreement, and the `multi_support` source's full claim set becomes the result instead (no
   information is discarded).
5. Otherwise (no source passed step 3's gates, or it doesn't contain the taxon): the original
   winner stands, exactly as today.

The peek is skipped entirely — falling straight through to "the winner stands" — when the winner
is `is_manual` (a human's call, or a device-side manual correction, is never second-guessed by an
automatic classifier below it) or when its verdict is `NOISE` (only a `SPECIES` winner can ever be
a member of an effectively-multi-support source's homogeneous-`SPECIES` set, so containment against
a `NOISE` verdict can never apply; asking would be pure overhead).

A `multi_support` source that is itself the *first* winner (e.g. `MANUAL` with several species,
or `BATDETECT2` when nothing above it has live claims) is returned immediately, exactly as today
— nothing below a winning `multi_support` result is ever consulted.

### 3.3 Worked example

Precedence order (unchanged): `MANUAL, EMT_MANUAL, EMT_GUANO, EMT_WAMD, EMT_FILENAME, BATDETECT2,
BATTYBIRDNET, FLEDERMAP_NOISE`.

- `EMT_GUANO` → PIPPIP (plain, SPECIES). `EMT_WAMD` → all `NO_ID` (skipped). `EMT_FILENAME` → no
  claims. `BATDETECT2` → `{PIPPIP, MYODAU}` (multi_support, live, homogeneous `SPECIES`, more than
  one claim). PIPPIP ∈ `{PIPPIP, MYODAU}` → result becomes `BATDETECT2`'s full set, not
  `EMT_GUANO`'s single PIPPIP.
- Same, but `EMT_GUANO` → MYOMYS instead. MYOMYS ∉ `{PIPPIP, MYODAU}` → `EMT_GUANO`'s MYOMYS
  stands; `BATDETECT2`'s set is not consulted further, and this recording still gets flagged for
  review as a disagreement (§4) — the peek only *suppresses a false disagreement*, it never hides
  a real one.
- `EMT_GUANO` → PIPPIP; `BATDETECT2` → `{PIPPIP}` only (a single claim, not effectively richer
  than the winner's own single claim). The singleton-claim gate in step 3 stops the peek here —
  `EMT_GUANO`'s claim stands as-is, and `BATDETECT2`'s row simply shows as an agreeing
  (non-disagreeing, per §4) claim rather than silently becoming the displayed source for no
  informational gain. Before this gate was added (found during final review), the override fired
  anyway in this exact case — the single most common scenario once `BATDETECT2` runs alongside a
  device's own auto-ID — needlessly flipping which source's row is shown as "current" for a result
  that named nothing new.
- `EMT_GUANO` → PIPPIP; `BATDETECT2` → `{PIPPIP, NOISE}` (a mixed set — `BATDETECT2` detected a
  species claim and, separately, a `NOISE`-sentinel claim, per §5's corrected invariant).
  `BATDETECT2` is not *effectively* multi-support for this recording (its claims aren't
  homogeneous `SPECIES`), so the peek's step 3 gate fails and `EMT_GUANO`'s PIPPIP stands — the
  walk never returns a `CurrentIdentification` mixing a `SPECIES` claim with a `NOISE` claim.
  `BATDETECT2`'s self-contradiction is exactly the kind of thing §4's disagreement check should
  still be able to flag (see §4's own worked case).

## 4. Disagreement voting (`review_flags.py`, `_disagreement_reason`)

Replaces the current flat "≥2 distinct `(verdict[, taxon])` keys across all eligible claims" check
with per-*source* vote-sets, compared pairwise:

1. For each source with at least one `Identification` on the recording, excluding `is_manual`
   sources entirely (unchanged — matches `_HUMAN_REVIEW_SOURCES` today) and excluding an
   `only_noise` source whose only claim is `NO_ID` (new — see §2): build its vote-set, the set of
   `_claim_key(...)` values across its live claims (a plain source's set is always a singleton; an
   effectively-multi-support source's set — see below — may have more than one member).
2. For every pair of remaining sources, they are **compatible** (do not trigger a flag between
   them) according to whether each is **effectively multi-support** for this recording — the same
   §5 gate §3.2 uses: `is_multi_support(source)` AND every one of its (already-filtered) live
   claims has verdict `SPECIES`. A `multi_support` source whose set mixes a `SPECIES` claim with a
   `NOISE` claim (§5) is treated, for this comparison, as an ordinary plain source — its
   self-contradiction becomes an ordinary disagreement against every other source instead of being
   silently absorbed by the equality/containment rules below, which are meant for a coherent
   multi-species answer, not a self-contradictory one.
   - **Both effectively multi-support**: compatible only if their vote-sets are **equal**. `{X,Y}` vs.
     `{Y,Z}` disagree; `{X,Y}` vs. `{X}` also disagree (not equal, even though one contains the
     other) — two multi-species sources must name exactly the same set to agree. Deliberately
     strict rather than "one set contains the other agrees": in an ideal world, two classifiers
     given the same audio would produce the same answer, so any gap between two multi-species
     sources — including one finding a strict subset of the other's calls — is itself worth a
     human glance ("is that extra species really there, or is the other model just less
     sensitive?"). A plain source can only ever name one species, so it not naming a second one
     the way a `multi_support` source did carries no such signal — hence the weaker containment
     rule for a plain-vs-`multi_support` pair below, instead of the same equality bar. This is a
     starting point, not a settled threshold: `BATTYBIRDNET` is opt-in and off by default today
     (`config.py`'s `battybirdnet_enabled`), so this rule is largely untested against two
     multi-species classifiers actually running together. If it floods the review queue with
     subset-only "disagreements" once both are enabled, weaken it then (e.g. to "compatible if one
     set is a subset of the other") rather than guessing now.
   - **Exactly one effectively multi-support**: compatible if the other source's single vote is a
     **member** of the multi-support source's set. `{X}` vs. `{X,Y}` agree; `{Z}` vs. `{X,Y}`
     disagree.
   - **Neither effectively multi-support**: compatible iff their sets are equal — the same
     comparison the current code already does for two plain sources (this is also what a
     self-contradictory `multi_support` source, downgraded by the gate above, falls back to).
3. If **any** pair of remaining sources is incompatible, `_disagreement_reason` returns
   `"Classifiers disagree"` (unchanged message).

This directly fixes gap 3 from §1 (a plain source's real `NO_ID` now votes as `{NO_ID}` and is
incompatible with another source's `{SPECIES: x}`, so it flags — `only_noise`'s `NO_ID` still
never votes at all, so it never contributes such a flag) and gap 4 (an effectively-multi-support
source's own several claims are one vote-set, not several competing ones, so they never disagree
with themselves, and the compatibility rule above is exactly what makes §3.2's suppressed override
also suppress the matching false disagreement — both sections use the same effective-multi-support
gate, so a case that isn't rich enough or coherent enough to win an override is never rich/coherent
enough to be exempted from disagreement voting either).

NOISE vs. SPECIES disagreement is unchanged when neither source is `multi_support`: neither is
excluded for a `NOISE` claim (only an `only_noise` source's `NO_ID` is), so `{NOISE}` vs.
`{SPECIES: x}` are never equal and always flag, exactly as today. A `multi_support` source's own
internal `NOISE`-vs-`SPECIES` self-contradiction (§5) is a *new* case this rewrite makes flaggable
that the original flat-key design could not distinguish from a genuine multi-species claim set:
`BATDETECT2` → `{SPECIES: PIPPIP, NOISE}` compared against `EMT_GUANO` → `{SPECIES: PIPPIP}` — the
effective-multi-support gate downgrades `BATDETECT2` to "neither effectively multi-support," so the
comparison falls to plain equality: `{PIPPIP}` ≠ `{PIPPIP, NOISE}` → incompatible → flags. Without
the gate, the old (pre-final-review) containment rule would have read `BATDETECT2` as
multi-support and found `{PIPPIP} ⊆ {PIPPIP, NOISE}` — compatible, silently losing the signal that
`BATDETECT2` contradicted itself on this recording.

## 5. Invariant this design relies on -- corrected 2026-09-17 after final review

An earlier version of this section claimed a `multi_support` source's live claim set is always
either exactly `{NO_ID}` or one-or-more `SPECIES`/sentinel-verdict claims, "never containing
`NOISE` alongside a species." **That is false, and was already contradicted by an existing
in-repo spec at the time this was written**:
`docs/superpowers/specs/2026-09-16-fledermap-battybirdnet-classifier-design.md`'s §7 documents,
as a deliberate and accepted design decision, that "a `NOISE` claim can coexist with a `SPECIES`
claim from the same classifier and the same recording" — they occupy different `taxon_id` slots
(`NOISE` at `taxon_id=None`, the species at its own real `taxon_id`), so
`uq_identification_source_claim` never forces them to compete. `materialize_claims`
(`services/classifier_detections.py`) keys its per-claim dedup on `effective_taxon_id` alone, so a
`NOISE`-sentinel detection (`None`) and a mapped species detection (a real id) land in different
slots of `best_by_taxon` and both reach `desired` — nothing prevents this today for
`BATDETECT2`/`BATTYBIRDNET`.

The corrected invariant: a `multi_support` source's live claim set is always either exactly
`{NO_ID}`, or one-or-more claims whose verdicts may be a mix of `SPECIES` (including
sentinel-mapped `NOISE` claims sharing the set with species claims, per the above) — i.e. genuinely
**mixed verdicts are possible**, not excluded by construction.

Both §3.2 and §4 must not treat every `multi_support` claim set as a coherent multi-species
answer — a set that mixes a `SPECIES` claim with a `NOISE` claim is the source contradicting
itself, not a richer answer to defer to. Both sections below now gate on an **effective
multi-support** condition instead of the raw `is_multi_support(source)` check: a source counts as
effectively multi-support for a given recording only when `is_multi_support(source)` is true AND
every one of its live claims has verdict `SPECIES` (homogeneous). A source whose current claim set
mixes verdicts is treated, for that recording only, as if it were a plain source — see §3.2 and §4
for what that means in each place.

## 6. `NOISE` vs. plain `NO_ID` compatibility — added 2026-09-17 after deployment

§4's "neither effectively multi-support: compatible iff sets equal" rule, as written, also made a
bare `{NOISE}` incompatible with a bare `{NO_ID}` — since gap 3 (§1, §4) made a plain source's
`NO_ID` vote as `{NO_ID}` rather than being skipped, two plain sources where one calls `NOISE` and
the other simply didn't clear its own threshold for anything now flag "Classifiers disagree." That
wasn't the intent: gap 3 was about a classifier confidently finding nothing where another
confidently finds a *species* — a real disagreement worth a look. A classifier calling `NOISE`
alongside another that abstains entirely (`NO_ID`) is not that; it's two classifiers agreeing
there's no confident species here, one of them just being explicit about why. `NOISE` vs. `SPECIES`
and `NO_ID` vs. `SPECIES` both still flag, unchanged — only the `NOISE`-vs-`NO_ID` pairing between
two otherwise-plain vote-sets is now compatible. `_compatible()`'s "neither effectively
multi-support" branch carries one exception for exactly this case.

## 7. Known limitations

- **`current_best.py`'s containment check and `review_flags.py`'s equality/containment check can
  disagree on an unmapped species claim, because they key claims differently.** §3.2's containment
  test compares bare `taxon_id`; `review_flags.py`'s `_claim_key` falls back to comparing
  `raw_label` when `taxon_id is None` (so two different unmapped codes aren't wrongly treated as
  agreeing just because neither resolved). Found during final review: if `EMT_GUANO` and
  `BATDETECT2` both emit the identical unmapped raw code (say `"XYZ"`, `taxon_id=None`) —
  `BATDETECT2`'s set being `{SPECIES(None, "XYZ"), SPECIES(t2)}` — `review_flags.py` sees them as
  compatible (the raw-label key is a member of `BATDETECT2`'s set) while `current_best.py`'s
  containment check does not fire (`None` is never in `{t2}`, the only non-`None` taxon id in the
  set), so `EMT_GUANO`'s narrow claim stands as the displayed result with no disagreement flag
  drawing attention to it. Needs two different automatic sources emitting the exact same
  unmapped raw code on the same recording to manifest — narrow, and not fixed by this spec.
  Unifying the two comparisons would mean extracting a shared claim-key helper both
  `current_best.py` and `review_flags.py` import from `domain/`, which is a real refactor beyond
  this spec's scope; logged to the Obsidian backlog as a follow-up idea instead. This is the same
  shape of accepted, documented limitation as the "NOISE can outrank an unmapped species... by raw
  confidence" case in `docs/superpowers/specs/2026-09-16-fledermap-battybirdnet-classifier-design.md`'s
  §7 — a real gap, deliberately left for a future pass rather than solved here.

## 7. Non-goals

- Reordering `_PRECEDENCE` — out of scope, unchanged by this spec.
- Merging two different `multi_support` sources' results together (e.g. `BATDETECT2` ∪
  `BATTYBIRDNET`) when both have live claims — the walk still stops at the first (nearest)
  `multi_support` winner exactly as it does today; only the *containment override* in §3.2 looks
  past a non-`multi_support` winner, never past a `multi_support` one.
- Any change to `services/manual_classification.py` or the manual-classification UI — `MANUAL`'s
  behavior (strong NoID, multi-species support) is unchanged by this spec; only `EMT_MANUAL` moves
  to match it.
- Any change to `_rarity_reason`/`_misattribution_reason` in `review_flags.py` — only
  `_disagreement_reason` is touched.
- **Weakening the multi_support-vs-multi_support equality rule (§4) to a subset check** — not
  done by this spec. Written down here for whenever it's actually needed: if running
  `BATDETECT2` and `BATTYBIRDNET` together in practice floods the review queue with
  subset-only "disagreements" (one model finding a strict subset of the other's species), the
  fix is changing that one branch of §4's compatibility rule from `set_a == set_b` to "one set
  is a subset of the other" — everything else in this spec (the containment rules for a
  plain-vs-`multi_support` pair, §3.2's peek) is unaffected by that change. Not implemented now
  because `battybirdnet_enabled` defaults to `False` (`config.py`), so the scenario this would
  fix is currently untested against real data.
