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
  a group-level tag — a genus like *Myotis*, a frequency class like `HiF`/`LoF`/`Hilo`, or
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
- "No ID" (someone explicitly reviewed this and found nothing) and "Unidentified" (nobody, human
  or automatic, has found anything yet) become distinct, separately filterable states — a direct
  consequence of `NO_ID` becoming meaningful only via a genuine `MANUAL` claim.
- A human making a manual call can see *why* the page shows what it shows — which raw per-source
  claim is currently driving the result, which was shadowed by a higher-precedence source, and
  which automatic `NO_ID` was ignored as passive — not just the resolved headline in isolation.
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
  codes for very-similar-sounding species pairs, e.g. `EPFUMYLU`). Seeded with only
  `Myotis`/`MYSP`, `Plecotus` (single-genus groupings, not couplets), `HiF`, `LoF`, `Hilo`, and
  `NOTBAT` (Design §1) — codes never invented (`CLAUDE.md`: `MYOSPP` was fabricated by an earlier
  plan and had to be removed; every code above is verified against NABat's own published page and
  recorded in `docs/references.md`, same bar as any other code in this project). `Plecotus` gets
  no code at all, confirmed absent from NABat's own list (checked directly, not just this
  design's own automated fetch) — expected, since NABat covers North America only and *Plecotus*
  doesn't occur there.
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
  common_name_en: Mouse-eared bats  # descriptive; NABat's own text for MYSP is "Unknown species in the Myotis genus" (see TaxonCode note below)
  common_name_de: Mausohren
- scientific_name: Plecotus
  rank: genus
  common_name_en: Long-eared bats  # or similar; final wording TBD at seed time -- no NABat text exists for this one (see below)
  common_name_de: Langohren

- scientific_name: HiF
  rank: phonic_group
  common_name_en: Various species with pulses having a minimum frequency higher than ~30 kHz  # NABat's own text (HighF/HiF)
- scientific_name: LoF
  rank: phonic_group
  common_name_en: Various species with pulses having a minimum frequency lower than ~30 kHz  # NABat's own text (LowF/LoF)
- scientific_name: Hilo
  rank: phonic_group
  common_name_en: Two or more bats from distinct frequency classes vocalizing simultaneously within a recording  # NABat's own text
- scientific_name: NOTBAT
  rank: phonic_group
  common_name_en: Not a bat  # NABat's own text
```

**Verified 2026-09-05 against [NABat's own species-codes page](https://www.nabatmonitoring.org/species-codes)**
(`docs/references.md` updated) — `MYSP`, `HiF`/`LoF`, `Hilo`, and `NOTBAT` are all genuine,
correctly-spelled NABat codes (the page's own text is quoted directly above as
`common_name_en` for the four non-genus ones, since there's no separate "real name" to give
them beyond NABat's own description — see the `TaxonCode` note below). One correction from
an earlier draft of this spec: NABat's actual capitalization is **`Hilo`**, not `HiLo`. No
equivalent code exists for `Plecotus` on that page — checked directly by Janna, not just this
design's own automated fetch (which twice under-extracted the page's table and can't be
trusted as a negative result on its own) — expected, since NABat covers North America only and
*Plecotus* doesn't occur there. `common_name_de`/`Plecotus`'s English wording are still TBD at
seed time (cosmetic; NABat itself is English-only and has nothing for `Plecotus` to translate).

`Myotis` and `Plecotus` get `rank: genus` because they genuinely are ones — both are standard
"can't get past genus level acoustically" cases in European/North American call-ID practice
(*Plecotus auritus*/*P. austriacus* in particular are notoriously close acoustically, same shape
of problem as `Myotis`, raised during brainstorming and deliberately NOT a couplet — a
single-genus grouping, unlike the paired-species "Couplets" category this spec already excludes).
NABat's frequency-split classes and "non-bat sound present" aren't a genus at all, hence the
separate `phonic_group` rank — both ranks flow through the identical mechanism below, so this
split is purely about correctness/display, not behavior.

**One consistent rule for `scientific_name` vs. `TaxonCode`, applied to every row above, same as
every existing species**: `scientific_name` always holds the taxon's real/canonical name;
a `TaxonCode` row exists only where a real, sourced device/standard code actually does.

- `Myotis` gets `TaxonCode(source="nabat", code="MYSP")` — a genuine, published NABat code.
- `Plecotus` gets **no code for now**. `MYSP` is specifically a NABat (North-American) vocabulary
  entry; Europe has no equivalent single continent-wide acoustic-ID standard to draw an
  equivalent code from, and this design does not invent one (`CLAUDE.md`'s `MYOSPP` lesson,
  again). This isn't a functional gap: nothing here needs to *auto-resolve* a code for it (no
  classifier emits one), so the manual tag editor's autocomplete — which searches
  `scientific_name` directly — finds "Plecotus" by name with no code needed. A real, sourced
  European code can be added as a `TaxonCode` later with no structural change.
- `HiF`/`LoF`/`Hilo`/`NOTBAT` are not organisms, so they have no separate "real name" apart from
  their own short label — `scientific_name` is that label itself (e.g. `"HiF"`). Each still gets
  a matching `TaxonCode(source="nabat", code="HiF")` (etc.) for consistency with every other row
  and so a future automatic classifier could resolve one the same way, rather than leaving that
  implicit.

Every code above (`MYSP`, `HiF`, `LoF`, `Hilo`, `NOTBAT`) is now verified against NABat's own
published page and recorded in `docs/references.md` (2026-09-05) — the "must be verified before
seeding" bar every other code in this project already meets (see Non-goals) is satisfied; nothing
here is blocked on further sourcing.

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
session lists (`web/views/sessions.py`) — use `.primary`/`.verdict` unchanged, no behavior change
for the common single-claim case. `_passes_verdict_filter` also uses `.verdict` unchanged, but its
surrounding logic changes for an unrelated reason — see §3a. Four other callers need real updates:

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

### 3a. The verdict filter gains a real "Unidentified" option, distinct from "No ID"

A consequence of §2 surfaced during brainstorming, not originally planned: since an automatic-only
`NO_ID` claim is now always skipped (passive) rather than ever becoming "current,"
`current_best_identification` can only return an actual `Verdict.NO_ID` result via a `MANUAL`
claim. Automatic-only `NO_ID` and "no identification at all" both now collapse to the same
`best is None` result.

This makes "No ID" and "no identification survives" meaningfully different things for the first
time: "No ID" becomes "a human explicitly reviewed this and found nothing identifiable," while
`best is None` covers both "literally nothing has ever run" and "every automatic classifier drew
a blank" — i.e. "nobody, human or otherwise, has found anything here." That's exactly a review
queue's "still needs a look" bucket, distinct from "already reviewed, confirmed empty."

Today, `_passes_verdict_filter` (`services/map_query.py`) already treats `best is None` as
equivalent to `Verdict.NO_ID` for filtering (decision P4-9, `2026-08-25-fledermap-phase4-map-
design.md`) — written when there was no way to distinguish them. This design amends P4-9: add a
dated deviation note there pointing at this spec, matching this project's established practice for
cross-spec revisions (e.g. `FLEDERMAP_MEDIA_ROOT`'s note in `CLAUDE.md`).

- **`web/params.py`'s `parse_verdict`**: return type widens from `Verdict | Literal["all"] | None`
  to `Verdict | Literal["all", "unidentified"] | None`, parsing a new `verdict=unidentified` query
  value — the same sentinel-string shape `parse_taxon_filter`'s existing `"unmapped"` already
  uses, not a new mechanism.
- **`_passes_verdict_filter`**: `"unidentified"` matches `best is None` exactly.
  `Verdict.NO_ID` now matches only `best is not None and best.verdict == Verdict.NO_ID` — no
  longer folding `None` into it. The **default view is unaffected**: with `verdict` omitted, only
  `Verdict.SPECIES` is shown either way, so P4-9's actual purpose ("hide noise/unidentified by
  default") is preserved unchanged — this only changes what an explicit "No ID" selection matches
  versus a new, separate "Unidentified" selection.
- **`map.html`'s verdict `<select>`**: one new `<option value="unidentified">Unidentified</option>`
  alongside the existing "Species only (default)"/"Noise"/"No ID"/"All".

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

### 5a. The "Identifications" box: bring it to the details page, annotate precedence status

Raised during brainstorming: a human making a manual call needs to see what each automatic
source actually claimed, and *why* one is driving the shown result while another isn't — active
vs. passive `NO_ID` (§2) being the concrete trigger, but the same need applies to any shadowed
claim.

This box (the raw per-source list, `{{ ident.source.value }}: {{ ident.raw_label or
ident.verdict.value }}`) exists today only in the drawer panel (`_recording_panel.html`) — **not**
on the recording-details page at all, found while placing the classifier box. Bring it to
`recording_details.html` too (near the classifier box, §5), and annotate each row with its
precedence status, computed from `best.claims` (§3) and the walk in §2:

- **Current** (bold): the row is one of `best.claims` — i.e. it's actually contributing to the
  shown headline/marker/filter result.
- **Passive** (muted, "— passive, ignored"): an automatic-source row whose own claim is `NO_ID`
  and was skipped per §2's rule — shown regardless of what ultimately won, since this describes
  the row's own status, not the overall outcome.
- **Shadowed by `<source>`** (muted): a real (non-`NO_ID`, or any `MANUAL`) claim that lost only
  because a higher-precedence source's claim won outright — `<source>` names whichever source is
  `best.primary.source`.
- **Superseded**: unchanged, existing strikethrough styling (`app.css`'s `.superseded`) — a past
  claim replaced by a newer one from the same source, an orthogonal concept to the three above
  (a superseded row is never current/passive/shadowed, since `current_best_identification` only
  ever considers non-superseded rows in the first place).

A row is in at most one of the four states; computing which one is a small pure function next to
`current_best_identification` (e.g. `identification_status(ident, best) -> Literal["current",
"passive", "shadowed", "superseded"]`), not template logic — keeps the precedence reasoning in one
tested place rather than duplicated as Jinja conditionals.

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
- `tests/test_params.py`'s `parse_verdict` tests: new `"unidentified"` case. `tests/test_map_view.py`
  (or wherever `_passes_verdict_filter` is tested): a recording with only automatic `NO_ID` claims
  matches `verdict=unidentified` but not `verdict=no_id`; a recording with a `MANUAL` `NO_ID`
  claim matches `verdict=no_id` but not `verdict=unidentified`; the default (verdict omitted) view
  excludes both, unchanged from today.
- New `tests/test_manual_classification.py`: `set_manual_classification`'s supersede-then-insert
  behavior for every transition (species → species, species → NO_ID, NO_ID → species, species →
  clear, clear → species), the concurrent-multi-`SPECIES`-insert regression from §4, and the two
  new `ValueError` cases (`SPECIES` with empty `taxon_ids`; non-`SPECIES` with non-empty
  `taxon_ids`) plus the route's 400 response for each.
- New tests for `identification_status` (§5a): each of the four states, including the two easy
  ones to get backwards — a passive automatic `NO_ID` when NOTHING else wins either (still
  "passive," not "current" by elimination) and a real claim from the *winning* source when
  `best.is_multi` (every one of `best.claims` is "current", not just `best.primary`). Plus a
  recording-details-page test asserting the "Identifications" box now renders there at all.
- `tests/test_seed.py` (or equivalent): the new `Myotis`/`Plecotus`/`HiF`/`LoF`/`Hilo`/`NOTBAT`
  taxa round-trip correctly, including `Plecotus` having no `TaxonCode` row at all; neither
  `rank="genus"` nor `rank="phonic_group"` breaks anything that
  currently assumes `rank="species"` (grep for any such assumption before implementing — none
  found during this design's own research, but the
  plan should re-confirm).
- `hatch run types:check` and the JS-side manual test coverage gap: this is another JS-only UI
  feature (per `CLAUDE.md`'s "no test infrastructure exists" note) — verify live per this
  project's established puppeteer-core technique, not skipped silently.

## Decisions

| # | Decision |
|---|---|
| MC-1 | Genus/group-level identifications (`Myotis`/`MYSP`, `Plecotus`, `HiF`/`LoF`/`Hilo`, `NOTBAT`) reuse the existing `SPECIES` verdict + `taxon_id` via `rank="genus"`/`rank="phonic_group"` `Taxon` rows — no new `Verdict` member, no migration for a new CHECK value. `scientific_name` always holds the real/canonical name; a `TaxonCode` only exists where a real, sourced code does (`Plecotus` gets none). |
| MC-2 | `NOISE` is always active (any source); `NO_ID` is passive only for automatic sources; `MANUAL` (any verdict, including `NO_ID`) is always active. |
| MC-3 | Manual `SPECIES`/group claims are additive with each other (multi-species files, plus `HiF`/`LoF`/`Hilo`/`NOTBAT` as additional additive tags); `NO_ID`/`NOISE` remain singleton and mutually exclusive with them within `MANUAL` — `NOTBAT` is deliberately NOT folded into `NOISE`'s exclusivity, since it's a claim about additional content, not the whole recording. |
| MC-4 | "No manual opinion" is represented as zero non-superseded `MANUAL` rows, not a stored value — identical in kind to how every other source's absence already works. |
| MC-5 | `current_best_identification` returns a `CurrentIdentification` wrapper (one or more claims) instead of a single `Identification \| None` — `.primary`/`.verdict` cover every caller that only cared about one claim; the taxon filter is the one caller that must check the full `.taxon_ids` set. |
| MC-6 | The manual-classification UI always submits the tag box's full current state; the server always supersedes-then-inserts rather than diffing client-side — same safety property `_apply_identifications` already relies on. |
| MC-7 | Region-restricted taxon lists, `^7eb251`'s broader NoID semantics, the classifier-disagreement filter, and NABat's fuller group-code vocabulary are explicitly out of scope (see Non-goals). |
| MC-8 | The verdict filter gains a distinct "Unidentified" option (`best is None`) alongside "No ID" (now `MANUAL`-only in practice) — amends decision P4-9 (`2026-08-25-fledermap-phase4-map-design.md`), which folded them together when there was no way to distinguish them. The default (SPECIES-only) view is unaffected. |
| MC-9 | The "Identifications" per-source breakdown box moves from drawer-panel-only to also appearing on the recording-details page, with each row annotated as current/passive/shadowed/superseded via a new pure `identification_status` helper — not template-side conditionals. |

## Open items

- `Plecotus`/`Myotis`'s `common_name_de` and `Plecotus`'s `common_name_en` — cosmetic wording,
  resolve at seed time (the four `phonic_group` rows now use NABat's own English text directly,
  see Design §1, so nothing further to resolve there).
- If a real, sourced European-equivalent code for `Plecotus` ever turns up, it's a pure
  `TaxonCode` addition with no structural change — not expected, but not ruled out either.
- Exact file(s) holding the existing `current_best_identification` tests (referenced in §6 by
  best guess) — confirm during planning.
- `map_query.site_detail`'s species tally changes from "one taxon per recording" to "one or more
  taxa per recording" (§3) — checked, not just flagged: `_site_panel.html` renders
  `species_counts` and `site.recording_count` as two independent lines, nothing asserts they sum
  to each other, so this is safe as designed. No follow-up needed.
