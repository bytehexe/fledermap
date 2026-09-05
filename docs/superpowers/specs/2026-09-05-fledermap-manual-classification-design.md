# Fledermap Manual Classification — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing an
implementation plan.
**Date:** 2026-09-05

## Problem

`IdSource.MANUAL` has existed since the domain vocabulary was written (`domain/codes.py`: "A
future UI-entered identification, never re-derived from the scanned file") but nothing writes it
— there is no way to classify a recording from the app itself. This is the single biggest hole in
the v1 backlog (`fledermap.manual` or so, tagged `release:v1`).

Scoping it turned out to be tangled with several other open backlog items that all touch the same
code (`current_best_identification`'s "one winner" contract):

- **`^a549ce`**: a `NO_ID` claim at a high-precedence source can shadow a real `SPECIES` answer
  at a lower-precedence one, because precedence is picked by *source*, not by *verdict content*.
- **Multi-species files**: manual review has already found files containing more than one bat
  species. Today's model has no way to assert that — `current_best_identification` always
  resolves to exactly one winning `Identification` row.
- **Hard-to-distinguish genera** (e.g. many *Myotis* calls): a human reviewer often cannot get
  more precise than genus level. There is no code system in this project for anything less
  precise than a full species (`CLAUDE.md`: "The Wildlife Acoustics list is species-level only —
  no genus or group codes exist").

Untangling these required establishing one coherent precedence/data model first; this spec is the
result of that brainstorming round.

## Goals

- A human can classify a recording from the recording-details page: pick one or more species (or
  a group-level tag — a genus like *Myotis*, a frequency class like `HiF`/`LoF`/`HiLo`, or
  "non-bat sound present"), mark it `No ID` or `Noise`, or clear back to "no manual opinion"
  (deferring to whatever the automatic sources say).
- A `NO_ID` claim at a high-precedence **automatic** source no longer shadows a real `SPECIES`
  answer at a lower-precedence automatic source (`^a549ce`).
- A manual `NO_ID`/`NOISE` claim, by contrast, is authoritative — a human looked and found
  nothing identifiable (or found noise), and that should shadow lower-precedence sources exactly
  like a manual species claim does today.
- A multi-species file can be represented (several manual species/group claims on one recording),
  and — this is the part that must not regress — **every one of those claims is findable by the
  taxon filter**, not just one arbitrarily-picked "winner."
- A hard-to-identify genus-level call, an unresolvable-but-real frequency class, or "there's also
  a non-bat sound in here" can all be recorded without inventing a fake species code, and each
  participates in filtering/display exactly like a real species.

## Non-goals

- **No new `Verdict` member.** Genus/group-level identifications reuse the existing `SPECIES`
  verdict + `taxon_id`, via `Taxon` rows with `rank="genus"` or `rank="phonic_group"` — see Design
  §1. `Taxon.rank` already exists in the schema (`"A species, genus, or phonic group"`) and is
  currently written but never read; this design is its first consumer, not a new concept.
- **No region-restricted taxon list for the manual classifier.** Deferred in favor of a better
  future idea raised during brainstorming: auto-derive from the recording's own known location
  (EU/NA) rather than a manual config knob. Tracked as its own follow-up.
- **`^7eb251`'s broader NoID-semantics question is untouched.** Whether Wildlife Acoustics' own
  `NoID` sentinel should be reinterpreted per-device (possibly via a new "not classified"
  sentinel) is a separate, more speculative design question. This spec only fixes the *precedence
  mechanics* around `NO_ID` (`^a549ce`), not what `NO_ID` fundamentally means.
- **No "classifiers disagree" filter.** A different feature, not required for manual
  classification to ship.
- **No attempt at NABat's full "Couplets and Groupings" vocabulary** (paired-species-combination
  codes for very-similar-sounding species pairs). Seeded with only `MYSP`, `HiF`, `LoF`, `HiLo`,
  and `NOTBAT` (Design §1) — never invented (`CLAUDE.md`: `MYOSPP` was fabricated by an earlier
  plan and had to be removed; every one of these must be verified against
  `docs/references.md`/NABat's real published list before being seeded, same bar as any other
  code in this project — see Open items for `NOTBAT`'s spelling specifically).
- **No changes to how automatic sources other than the `NO_ID`-precedence fix behave.** EMT
  ingestion, `resolve_code`, `commit_scan`'s supersession logic for `_EMT_SOURCES` are unchanged.

## Design

### 1. Genus/group taxa reuse the existing `SPECIES` pathway

`Taxon.rank` (`String(16)`) is currently always `"species"` and read nowhere in the codebase —
confirmed by grep before writing this section. Its docstring already names two other cases this
design is the first to actually use: `"A species, genus, or phonic group."` Add:

```yaml
- scientific_name: Myotis
  rank: genus
  common_name_en: Mouse-eared bats  # or similar; final wording TBD at seed time
  common_name_de: Mausohren

- scientific_name: HiF
  rank: phonic_group
  common_name_en: High-frequency bat (unidentified)
- scientific_name: LoF
  rank: phonic_group
  common_name_en: Low-frequency bat (unidentified)
- scientific_name: HiLo
  rank: phonic_group
  common_name_en: Mixed high/low-frequency bat activity (unidentified)
- scientific_name: NOTBAT
  rank: phonic_group
  common_name_en: Non-bat sound present
```

(`common_name_de`/exact English wording TBD at seed time, same as `Myotis` above.) `Myotis` gets
`rank: genus` because it genuinely is one; NABat's frequency-split classes and "non-bat sound
present" aren't a genus at all, hence the separate `phonic_group` rank — both ranks flow through
the identical mechanism below, so this split is purely about correctness/display, not behavior.

Each gets a `TaxonCode(source="nabat", code=..., taxon_id=<that row>)`, once every code is
verified against an authoritative NABat source and recorded in `docs/references.md` (same bar as
every other code in this project — see Non-goals; `NOTBAT`'s exact spelling in particular needs
confirming, see Open items).

**`NOTBAT` is deliberately additive, not exclusive like `NOISE`.** `Verdict.NOISE` is a claim
about the *whole recording* ("this file is noise, nothing else to find"); `NOTBAT`-as-a-tag is a
claim about *additional* content — "there's also a clearly non-bat sound in this file," alongside
whatever species/group chips are already there. Confirmed during brainstorming: for these
recordings there's always more that could be noted, so nothing here tries to represent
"review complete" — the absence of further chips just means nothing further was noted, not an
assertion that nothing else exists.

No other code changes are needed for any of this to work correctly:

- `recording_headline()` already just returns `taxon.scientific_name` for a resolved taxon —
  `"Myotis"`/`"HiF"`/`"NOTBAT"` all read correctly with no special-casing.
- `list_taxa()` and the taxon filter already operate on `taxon_id` generically — a `MYSP`- or
  `HiF`-tagged recording becomes findable by selecting it in the dropdown exactly like any
  species.
- `resolve_code`/`current_best_identification` never branch on rank today and don't need to.

This means none of these are a new axis in the precedence model below — each is just another
`SPECIES`-verdict claim with a `taxon_id`, and "multiple current species/group claims" (§3) covers
all of them automatically. A manual tag box holding `Pipistrellus pipistrellus` + `HiF` + `NOTBAT`
is three ordinary additive `SPECIES` rows.

### 2. `current_best_identification` rewrite: active vs. passive `NO_ID`

Current behavior (`services/current_best.py`): walk `_PRECEDENCE` in order, return the
highest-precedence source's claim (tie-broken by most recent `first_seen_at`) if that source has
*any* non-superseded claim at all — regardless of verdict.

New rule, established across this brainstorming round:

- **`NOISE` is always active**, from any source. A source with a `NOISE` claim wins outright and
  stops the walk — no passive/active split for `NOISE` like `NO_ID` gets, since "confidently
  noise" is a positive claim, not an admission of uncertainty.
- **`NO_ID` is passive only for automatic sources** (`EMT_MANUAL`, `EMT_GUANO`, `EMT_WAMD`,
  `EMT_FILENAME`). A source whose non-superseded claims are *purely* automatic `NO_ID` is skipped
  entirely — the walk continues to the next source in precedence, so a real `SPECIES` answer
  further down can surface (`^a549ce`, fixed).
- **`MANUAL` is unconditionally active**, including its own `NO_ID`. Any non-superseded `MANUAL`
  claim, of any verdict, wins outright and stops the walk — a human's `NO_ID` judgment is
  deliberate and authoritative, unlike an automatic classifier's admission of uncertainty.
- **If the winning source is `MANUAL` with multiple non-superseded `SPECIES` claims**, all of
  them are "current" — see §3 for how that's represented back to callers.

```python
def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    candidates = [i for i in recording.identifications if i.superseded_at is None]
    for source in _PRECEDENCE:
        matches = [i for i in candidates if i.source == source]
        if not matches:
            continue
        if source != IdSource.MANUAL and all(m.verdict == Verdict.NO_ID for m in matches):
            continue  # passive automatic NO_ID -- fall through to the next source
        return CurrentIdentification.from_matches(matches)
    return None
```

(`NOISE`'s always-active behavior falls out of this for free: a `NOISE` claim is never
all-`NO_ID`, so its source is never skipped.)

### 3. `current_best_identification`'s return type changes shape

Every existing caller (`recording_headline`, `map_query.filtered_recordings`,
`map_query.site_detail`, the recording-details/session-detail/drawer-panel views) currently
expects a single `Identification | None` and reads `.verdict`/`.taxon_id`/`.raw_label` directly.
That single-winner assumption breaks for a multi-species `MANUAL` result.

Introduce a small result type in `services/current_best.py`:

```python
@dataclass(frozen=True)
class CurrentIdentification:
    claims: tuple[Identification, ...]  # always >= 1; >1 only possible for MANUAL SPECIES

    @property
    def is_multi(self) -> bool:
        return len(self.claims) > 1

    @property
    def primary(self) -> Identification:
        """The single representative claim for headline/marker-color purposes --
        first-added (lowest first_seen_at), matching how ties within one source
        already break today."""
        return min(self.claims, key=lambda i: i.first_seen_at or _EPOCH)

    @property
    def verdict(self) -> Verdict:
        return self.primary.verdict

    @property
    def taxon_ids(self) -> frozenset[int]:
        return frozenset(c.taxon_id for c in self.claims if c.taxon_id is not None)

    @classmethod
    def from_matches(cls, matches: Sequence[Identification]) -> CurrentIdentification:
        return cls(claims=tuple(matches))
```

Full caller sweep (grepped, not guessed — every `current_best_identification` call site and every
direct `.verdict`/`.taxon_id` read in `src/fledermap/`):

Callers that only ever cared about a single verdict/taxon — the drawer panel (`web/views/map.py`),
session lists (`web/views/sessions.py`), `_passes_verdict_filter` — use `.primary`/`.verdict`
unchanged, no behavior change for the common single-claim case. Four callers need real updates:

- **`recording_headline`**: `"Multiple Species"` when `best.is_multi`, a dedicated fixed marker
  color (not hash-derived — a new constant alongside the existing taxon palette in
  `web/static/app.js`'s color logic, distinct from any real taxon's color so it can't collide),
  otherwise unchanged (single taxon's name, or the existing unmapped-species/verdict-value
  fallback).
- **`map_query.filtered_recordings`**'s taxon-filter branch: match if `taxon_id in
  best.taxon_ids` instead of `best.taxon_id == taxon_id` — this is the actual bug fix. The
  `taxon_exclude`/`"unmapped"` branches adapt the same way (unmapped becomes "every claim in
  `best.claims` is `SPECIES`-verdict with no `taxon_id`").
- **`map_query.site_detail`**'s per-site species tally (`counts[best.taxon_id] += 1`, one count
  per recording): iterate `best.taxon_ids` and increment every one of them, not just a single
  pick — a genuine multi-species recording is real evidence of every species it contains at that
  site, same reasoning as the filter fix. (A recording's count contribution changes from "exactly
  one taxon" to "one or more," so any assumption downstream that a site's species counts sum to
  its `recording_count` needs rechecking during planning — flagged as an open item below.)
- **`web/api/geojson.py`** (missed in earlier drafting — caught by the full grep sweep): this is
  what actually drives the map markers' colors, via `app.js`'s `colorForTaxon`. Add a
  `"multi_species": best.is_multi` property alongside the existing `"taxon_id"`/`"verdict"` ones
  (kept as `best.primary.taxon_id`/`best.primary.verdict.value` for backward compatibility with
  any other JS reading them); `app.js`'s marker-color function checks `multi_species` first, before
  falling into the existing per-taxon branch.

**`recording_details_page` (`web/views/recording_detail.py`) also needs a real update**, not just
a mechanical `.primary` swap: today it fetches one `Taxon` via `best.taxon_id` purely for the
headline. It now also needs every `Taxon` in `best.taxon_ids` to pre-populate the classifier box's
tag editor with the recording's current manual claims (§5) — the headline lookup and the editor's
initial state are two different needs that happen to have shared one field before.

### 4. Manual classification storage

New service function, `services/manual_classification.py` (mirrors `commit_scan`'s
create/supersede shape from `services/ingest.py`, but driven by a UI submission instead of a
scan):

`verdict` is `Verdict | None` rather than always-required — `None` means "clear to no opinion" as
its own explicit input, not an implicit consequence of passing `Verdict.SPECIES` with an empty
`taxon_ids` (that combination is now a `ValueError`, not a silent no-op, so a client bug sending
an empty list by accident fails loudly instead of quietly clearing someone's classification):

```python
def set_manual_classification(
    session: OrmSession,
    recording: Recording,
    *,
    verdict: Verdict | None,  # None = clear to "no manual opinion"
    taxon_ids: Sequence[int] = (),  # required (non-empty) iff verdict is SPECIES; empty otherwise
) -> None:
    if verdict == Verdict.SPECIES and not taxon_ids:
        raise ValueError("verdict=SPECIES requires at least one taxon_id")
    if verdict != Verdict.SPECIES and taxon_ids:
        raise ValueError("taxon_ids is only meaningful for verdict=SPECIES")

    now = datetime.now(UTC)
    existing = [i for i in recording.identifications
                if i.source == IdSource.MANUAL and i.superseded_at is None]
    for ident in existing:
        ident.superseded_at = now

    if verdict in (Verdict.NO_ID, Verdict.NOISE):
        recording.identifications.append(
            Identification(source=IdSource.MANUAL, verdict=verdict, first_seen_at=now),
        )
    elif verdict == Verdict.SPECIES:
        for taxon_id in taxon_ids:
            recording.identifications.append(
                Identification(
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_id,
                    first_seen_at=now,
                ),
            )
    # verdict is None: "clear" -- existing MANUAL rows already superseded above, nothing new
    # inserted.
    session.commit()
```

This one function encodes the whole within-`MANUAL` exclusivity rule from brainstorming: calling
it always supersedes *every* standing `MANUAL` claim first, then inserts exactly what the new
state calls for — `NO_ID`/`NOISE` as a singleton, one or more `SPECIES` rows for the tag box's
current chips, or nothing at all for "no opinion." The UI never needs to compute a diff against
prior state itself (§5) — it just POSTs the tag box's current contents every time, and the
service's supersede-then-insert is the safe way to apply that under concurrent access (matching
`_apply_identifications`'s own key-based approach).

`uq_identification_source_claim` (`recording_id, source, source_version, raw_label`) already
permits this: multiple `MANUAL` rows per recording differ in `taxon_id`, which isn't part of that
constraint, and `source_version`/`raw_label` are both `NULL` for every manual row (already true
today) — `postgresql_nulls_not_distinct=True` was written for exactly this "manual annotations
report no version" case, so it does *not* block multiple concurrent manual rows differing only in
`taxon_id`. Confirmed by inspection; add a regression test asserting two manual `SPECIES` rows
for the same recording insert cleanly (§6).

### 5. UI: the classifier box

New template fragment on the recording-details page (`recording_details.html`) only (confirmed
during brainstorming: not the drawer panel — manual classification is a deliberate, close-review
action, not a quick map-browsing one; classifying something found on the map means one extra
click through to Details, which is an acceptable v1 trade-off). Placement: directly below the
existing meta line (timestamp/site/"Show on map"/"Session") and above the tool toolbar
(Default/Ruler/Lock view) — the meta line is read-only context about what the recording *is*, the
classifier box is the one editable, consequential thing on the page, and the toolbar below it is
about *how you look at* the render rather than *what it is*. Always visible, not collapsed behind
a disclosure, matching the favourite button's own already-visible-by-default precedent. If this
placement turns out not to fit well in practice, the agreed fallback is moving it below the audio
controls instead — noted here so a future revision isn't guessing at the alternative.

Follows this project's existing fragment-plus-htmx-POST pattern
(`_detail_favourite_button.html`/`toggle_favourite`):

- A **tag multiselect** for species/group taxa. Backed by a small inline JSON search index (one
  entry per `Taxon`: `scientific_name`, `common_name_en`, `common_name_de`, every mapped
  `TaxonCode.code`) — small enough (~70 taxa) to inline in the page rather than a per-keystroke
  endpoint, matching this project's existing "no frontend build step" scale assumption. Typing
  matches against *all* of those fields; selecting an entry always renders the chip as
  `scientific_name`, regardless of which field matched. Removing a chip is a plain click.
- **No ID** / **Noise** buttons, and a **clear** action ("no manual opinion", sends `verdict=None`
  per §4). Selecting either button clears any species/group chips (mutually exclusive, per
  brainstorming); adding a species chip while No ID/Noise is active clears that selection back to
  the tag box. This mutual exclusion is enforced client-side for immediate feedback, but the real
  guarantee is server-side: `set_manual_classification` (§4) now raises on an inconsistent
  combination rather than silently coercing it, and the route translates that into a 400 response
  — the client can send a malformed request (a bug, not a normal user path) but it can never
  *persist* an inconsistent state.
- Saving POSTs the box's full current state (`verdict` + `taxon_ids[]`, empty/omitted `verdict`
  for "clear") to a new route, `POST /recordings/<audio_hash>/manual-classification`, handled the
  same way `toggle_favourite` is: re-render the affected fragment(s) via htmx.

### 6. Test impact

- `tests/test_current_best.py` (or wherever the existing precedence tests live — confirm exact
  file before implementing): new cases for automatic-`NO_ID`-is-passive (`^a549ce`), `NOISE`
  always active regardless of source, `MANUAL NO_ID` shadowing a lower-precedence `SPECIES`
  claim, and multi-`SPECIES` `MANUAL` claims all surfacing via `.claims`/`.taxon_ids`.
- `tests/test_map_view.py`/wherever `filtered_recordings`'s taxon filter is tested: a recording
  with two manual `SPECIES` claims is found by filtering on *either* taxon, and by neither
  before adding them. Also `site_detail`'s species tally: a multi-species recording increments
  every one of its taxa, not just one.
- GeoJSON API test coverage: a multi-species recording's feature carries `multi_species: true`;
  `app.js`'s marker-color function picks the dedicated color for it (live-verified per this
  project's puppeteer-core technique, same as any other JS-only change).
- New `tests/test_manual_classification.py`: `set_manual_classification`'s supersede-then-insert
  behavior for every transition (species → species, species → NO_ID, NO_ID → species, species →
  clear, clear → species), the concurrent-multi-`SPECIES`-insert regression from §4, and the two
  new `ValueError` cases (`SPECIES` with empty `taxon_ids`; non-`SPECIES` with non-empty
  `taxon_ids`) plus the route's 400 response for each.
- `tests/test_seed.py` (or equivalent): the new `Myotis`/`HiF`/`LoF`/`HiLo`/`NOTBAT` taxa
  round-trip correctly; neither `rank="genus"` nor `rank="phonic_group"` breaks anything that
  currently assumes `rank="species"` (grep for any such assumption before implementing — none
  found during this design's own research, but the
  plan should re-confirm).
- `hatch run types:check` and the JS-side manual test coverage gap: this is another JS-only UI
  feature (per `CLAUDE.md`'s "no test infrastructure exists" note) — verify live per this
  project's established puppeteer-core technique, not skipped silently.

## Decisions

| # | Decision |
|---|---|
| MC-1 | Genus/group-level identifications (`MYSP`, `HiF`/`LoF`/`HiLo`, `NOTBAT`) reuse the existing `SPECIES` verdict + `taxon_id` via `rank="genus"`/`rank="phonic_group"` `Taxon` rows — no new `Verdict` member, no migration for a new CHECK value. |
| MC-2 | `NOISE` is always active (any source); `NO_ID` is passive only for automatic sources; `MANUAL` (any verdict, including `NO_ID`) is always active. |
| MC-3 | Manual `SPECIES`/group claims are additive with each other (multi-species files, plus `HiF`/`LoF`/`HiLo`/`NOTBAT` as additional additive tags); `NO_ID`/`NOISE` remain singleton and mutually exclusive with them within `MANUAL` — `NOTBAT` is deliberately NOT folded into `NOISE`'s exclusivity, since it's a claim about additional content, not the whole recording. |
| MC-4 | "No manual opinion" is represented as zero non-superseded `MANUAL` rows, not a stored value — identical in kind to how every other source's absence already works. |
| MC-5 | `current_best_identification` returns a `CurrentIdentification` wrapper (one or more claims) instead of a single `Identification \| None` — `.primary`/`.verdict` cover every caller that only cared about one claim; the taxon filter is the one caller that must check the full `.taxon_ids` set. |
| MC-6 | The manual-classification UI always submits the tag box's full current state; the server always supersedes-then-inserts rather than diffing client-side — same safety property `_apply_identifications` already relies on. |
| MC-7 | Region-restricted taxon lists, `^7eb251`'s broader NoID semantics, the classifier-disagreement filter, and NABat's fuller group-code vocabulary are explicitly out of scope (see Non-goals). |

## Open items

- Exact wording for every new taxon's `common_name_en`/`common_name_de` — cosmetic, resolve at
  seed time.
- Confirm `MYSP`, `HiF`, `LoF`, `HiLo`, and `NOTBAT`'s exact source/spelling against NABat's real
  published list (and add each to `docs/references.md`) before seeding — not yet done as of this
  spec. `NOTBAT` in particular needs its spelling settled (this spec used the spelling from an
  earlier backlog note verbatim, unverified — could plausibly be `NOBAT` instead).
- Exact file(s) holding the existing `current_best_identification` tests (referenced in §6 by
  best guess) — confirm during planning.
- `map_query.site_detail`'s species tally changes from "one taxon per recording" to "one or more
  taxa per recording" (§3) — checked, not just flagged: `_site_panel.html` renders
  `species_counts` and `site.recording_count` as two independent lines, nothing asserts they sum
  to each other, so this is safe as designed. No follow-up needed.
