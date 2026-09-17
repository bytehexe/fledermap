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
   look further than this one, even if it fails the containment check below — the highest
   precedence source that actually disagrees is the one that should win, and stopping there means
   a real disagreement still gets flagged (§4) instead of being silently resolved by digging for
   an even-lower-precedence match.
3. If that source's claim set contains the winner's `taxon_id`: the winner is **not** a real
   disagreement, and the `multi_support` source's full claim set becomes the result instead (no
   information is discarded).
4. Otherwise (no `multi_support` source found below, or it doesn't contain the taxon): the
   original winner stands, exactly as today.

The peek is skipped entirely — falling straight through to "the winner stands" — when the winner
is `is_manual` (a human's call, or a device-side manual correction, is never second-guessed by an
automatic classifier below it) or when its verdict is `NOISE` (a `multi_support` source's claim
set is always either `{NO_ID}` or one-or-more `SPECIES`/sentinel claims — see §5 — so containment
against a `NOISE` verdict can never apply; asking would be pure overhead).

A `multi_support` source that is itself the *first* winner (e.g. `MANUAL` with several species,
or `BATDETECT2` when nothing above it has live claims) is returned immediately, exactly as today
— nothing below a winning `multi_support` result is ever consulted.

### 3.3 Worked example

Precedence order (unchanged): `MANUAL, EMT_MANUAL, EMT_GUANO, EMT_WAMD, EMT_FILENAME, BATDETECT2,
BATTYBIRDNET, FLEDERMAP_NOISE`.

- `EMT_GUANO` → PIPPIP (plain, SPECIES). `EMT_WAMD` → all `NO_ID` (skipped). `EMT_FILENAME` → no
  claims. `BATDETECT2` → `{PIPPIP, MYODAU}` (multi_support, live). PIPPIP ∈ `{PIPPIP, MYODAU}` →
  result becomes `BATDETECT2`'s full set, not `EMT_GUANO`'s single PIPPIP.
- Same, but `EMT_GUANO` → MYOMYS instead. MYOMYS ∉ `{PIPPIP, MYODAU}` → `EMT_GUANO`'s MYOMYS
  stands; `BATDETECT2`'s set is not consulted further, and this recording still gets flagged for
  review as a disagreement (§4) — the peek only *suppresses a false disagreement*, it never hides
  a real one.

## 4. Disagreement voting (`review_flags.py`, `_disagreement_reason`)

Replaces the current flat "≥2 distinct `(verdict[, taxon])` keys across all eligible claims" check
with per-*source* vote-sets, compared pairwise:

1. For each source with at least one `Identification` on the recording, excluding `is_manual`
   sources entirely (unchanged — matches `_HUMAN_REVIEW_SOURCES` today) and excluding an
   `only_noise` source whose only claim is `NO_ID` (new — see §2): build its vote-set, the set of
   `_claim_key(...)` values across its live claims (a plain source's set is always a singleton; a
   `multi_support` source's set may have more than one member).
2. For every pair of remaining sources, they are **compatible** (do not trigger a flag between
   them) according to:
   - **Both `multi_support`**: compatible only if their vote-sets are **equal**. `{X,Y}` vs.
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
   - **Exactly one `multi_support`**: compatible if the plain source's single vote is a **member**
     of the `multi_support` source's set. `{X}` vs. `{X,Y}` agree; `{Z}` vs. `{X,Y}` disagree.
   - **Neither `multi_support`**: compatible iff their (singleton) sets are equal — the same
     comparison the current code already does for two plain sources.
3. If **any** pair of remaining sources is incompatible, `_disagreement_reason` returns
   `"Classifiers disagree"` (unchanged message).

This directly fixes gap 3 from §1 (a plain source's real `NO_ID` now votes as `{NO_ID}` and is
incompatible with another source's `{SPECIES: x}`, so it flags — `only_noise`'s `NO_ID` still
never votes at all, so it never contributes such a flag) and gap 4 (a `multi_support` source's own
several claims are one vote-set, not several competing ones, so they never disagree with
themselves, and the compatibility rule above is exactly what makes §3.2's suppressed override also
suppress the matching false disagreement).

NOISE vs. SPECIES disagreement is unchanged: neither source is excluded for a `NOISE` claim (only
an `only_noise` source's `NO_ID` is), so `{NOISE}` vs. `{SPECIES: x}` are never equal and always
flag, exactly as today.

## 5. Invariant this design relies on

A `multi_support` source's live claim set for one recording is always either exactly `{NO_ID}` or
one-or-more `SPECIES`/sentinel-verdict claims — never a mix, and never containing `NOISE` alongside
a species. This already holds by construction in `materialize_claims` (`desired` is either the
all-`NO_ID` singleton or the list built from `best_by_taxon`, never both) and in
`manual_classification.py`'s manual-claim writer. Both §3.2 and §4 depend on it: it's what makes
"does verdict `SPECIES` need a peek" and "is a `multi_support` set's containment check meaningful"
well-defined without needing to handle a mixed set. If a future `multi_support` source could ever
produce mixed verdicts in one set, this section would need revisiting.

## 6. Non-goals

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
