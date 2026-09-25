# Fledermap Recordings Search — Design

**Status:** draft
**Date:** 2026-09-25

## Problem

There is no way to find a recording except by browsing the map (bbox/taxon/verdict/date filters)
or a session's file list. Nothing lets a user jump straight to a recording by filename, a
free-text note in its comment, a site name, or a combination of these — the original backlog item
("google/jql style" search) was open-ended; this spec narrows it to a buildable v1 slice, per the
2026-09-25 scope-sketch chat already logged in the Obsidian backlog under `#Search`.

## Goals

- A `/search` page: one free-text query box + a whitelisted field-query grammar, returning a
  paginated list of matching recordings, each linking straight to its own `/recordings/<hash>`
  page.
- Composes with the app's existing filter semantics (`services/map_query.py`) rather than
  duplicating them — a `verdict:`/`species:` field means exactly what the map's own verdict/taxon
  filters already mean.
- Not injectable: every field is a fixed, typed whitelist; nothing free-text ever reaches SQL
  except as a bound parameter.

## Non-goals (explicitly deferred, not dropped)

- Sessions search (original item covered both; recordings-only for v1).
- `recorded:>...` date-range queries (needs range grammar).
- `AND`/`OR`/negation, nested field values, wildcards.
- Tags (`tags` backlog item doesn't exist as a feature yet).
- A parser-generator/PEG library — considered and rejected; see "Grammar and tokenizer" below.

## Grammar

Whitespace-tokenized. A token is one of, in this priority:

1. `field:"quoted value"` — a field query with a quoted value (spaces allowed inside).
2. `field:bareword` — a field query with an unquoted value (no spaces).
3. `"quoted phrase"` — a free-text phrase (spaces allowed inside).
4. anything else — a free-text word.

`field` is only ever treated as a field query if it's one of the fixed whitelisted names below;
any other `word:value`-shaped token (a timestamp `12:30`, a URL fragment, an unrecognized prefix)
falls through to case 4 as an ordinary free-text word, not a parse error — same reasoning as
`domain/codes.py`'s unmapped-species handling: an unrecognized token resolves to "just text", never
a hard failure. It does, however, add a `ParsedQuery.warnings` entry (see "Tokenizer" and "UI"
below) — degrading silently would leave a user who mistyped `spceies:PIPPIP` wondering why their
field query was ignored, which is worse than a plain "unknown field" note. A `field:` token whose
prefix genuinely isn't meant as a field at all (`12:30`, a URL) still degrades the same way, so
this warning is a heuristic nudge ("did you mean one of: …"), not a hard validation error — it
never blocks the search from running.

All tokens AND together. No OR, no negation, no operator precedence — this is why the grammar
needs no recursive/nested structure and no parser-generator library (see below).

### Field whitelist

| Field | Meaning | Maps onto |
|---|---|---|
| `species:<value>` | exact taxon lookup, not fuzzy — see "Species value resolution" below | `filtered_recordings`'s existing `taxon_id` param, after resolving `<value>` to a taxon_id |
| `annotation:<source>` | `bd2`/`bbn`/`emt`/`manual` — has a claim from that source | `filtered_recordings`'s existing `source: IdSource \| Literal["emt"] \| None` param, after mapping the shorthand (`bd2`→`IdSource.BATDETECT2`, `bbn`→`IdSource.BATTYBIRDNET`, `manual`→`IdSource.MANUAL`, `emt`→the existing `"emt"` sentinel) — this is exactly the "has a claim from source X, or any combined-EMT source" check the map's own Source filter already makes, so no new SQL |
| `site:<name>` | site name, exact | new `Site.name ==` join in `search.py` |
| `verdict:<value>` | `species`/`no_id`/`noise`/`unidentified`/`all` — see the note on `verdict:`'s vocabulary below | `filtered_recordings`'s existing `verdict` param, via the existing `web/params.py`'s `parse_verdict` |
| `is:favourite` / `is:flagged` / `is:commented` | boolean namespace, room for a future boolean without a new prefix | `favourite_only` (existing `filtered_recordings` param) / new `flagged_only` param added to `filtered_recordings` (same shape as `favourite_only` — a plain existing boolean column, not something `search.py` needs its own SQL for) / new EXISTS-on-`Annotation` clause in `search.py` (`source == AnnotationSource.COMMENT`, genuinely new — no equivalent exists anywhere else) |

Every field above except `site:` draws its value from a closed vocabulary (a fixed enum, or the
species-resolution rules below) — see "Warnings: silent divergence always surfaces" below for what
happens when a value doesn't fit that vocabulary. `site:<name>` has no such vocabulary (site names
are open-ended), so a `site:` value matching zero sites is ordinary "no results," the same as any
filter that legitimately matches nothing — not a divergence, nothing to warn about.

### Species value resolution

`species:<value>` accepts three representations, tried in this order — each is still an *exact*,
deterministic match against one specific taxon (or none), never a fuzzy/ranked lookup, so the
"a wrong match is worse than a missing one" principle from `domain/codes.py` still holds:

1. **A code** — exact `(source, code)` lookup via the existing `domain/codes.py` machinery, e.g.
   `species:PIPPIP`. Unchanged from the original spec.
2. **A full scientific name**, case-insensitive exact match against `Taxon.scientific_name`, e.g.
   `species:"Pipistrellus pipistrellus"`. Safe as an exact match because `scientific_name` already
   carries a DB-level `unique` constraint (`store/models.py:337`) — at most one taxon can match.
3. **An abbreviated genus form**, `"G. epithet"` (a single capital letter, a period, then the
   species epithet), e.g. `species:"P. pipistrellus"` — matched against taxa whose
   `scientific_name`'s genus starts with that letter and whose epithet equals the given one
   exactly, case-insensitive. **If more than one taxon matches** (e.g. two genera in the taxa files
   both start with the same letter and happen to share an epithet), or **if none matches**, this
   resolves to *no unique taxon* — see "Warnings" below for what that does to the query, which is
   not the same as silently returning zero rows.

Common names (English/German) are deliberately **not** a `species:` representation — a common name
is already searchable via plain free text (see below), and keeping `species:` limited to
code/scientific-name forms keeps it a precise-identifier field rather than doubling the ambiguity
surface a common name can carry across the `taxa_*.yaml` files.

### Warnings: silent divergence always surfaces

**Any token whose field name is recognized but whose value doesn't resolve unambiguously within
that field's vocabulary is treated exactly like an unrecognized field name: reclassified as a
literal free-text word instead of applying as a filter, with a warning naming what happened.**
This is one invariant applied uniformly, not a warning bolted onto each field individually —
raised explicitly during design after the first draft only warned for an unrecognized field
prefix and a mismatched quote, leaving `species:`'s ambiguous/not-found case (and the equivalent
cases for `annotation:`/`verdict:`/`is:`) silently returning fewer or zero results with no
indication the value hadn't actually been understood as typed. Concretely, in addition to the two
tokenizer-level cases already covered above:

- `annotation:<value>` where `<value>` isn't one of `bd2`/`bbn`/`emt`/`manual`.
- `verdict:<value>` where `<value>` isn't a member of the `Verdict` enum.
- `is:<value>` where `<value>` isn't `favourite`/`flagged`/`commented`.
- `species:<value>` where resolution (above) finds zero or more than one taxon.

Each of these four is checkable without a DB round-trip except the last, so the first three are
caught in `parse_search_query` itself (appending to `ParsedQuery.warnings` and demoting the token
to `free_text_terms`); `species:` resolution needs a session and happens later, inside
`search_recordings` (see "Backend" below) — its warning is appended to the same list at that point,
and the unresolved value is folded into that call's free-text matching rather than left as a
`taxon_id` filter that can only ever match nothing. `site:<name>` matching zero sites is exempt, per
the field-whitelist note above — there's no closed vocabulary for a value to diverge from.

### Free text

Unfielded words/phrases match against filename, comment text, site name, and species name — each
term AND'd against the others, each term itself an OR across those four targets:

- **Filename**: `Recording` has no `filename` column, only `path` (the full archive-relative
  path, `store/models.py:49`) — a term matches via `Recording.path.ilike(f"%{term}%")`. A term
  appearing in the filename is necessarily a substring of the full path too, so matching on `path`
  finds the same recordings a real `filename` column would; only *display* (see "UI" below) needs
  the actual basename, computed with `os.path.basename(recording.path)`.
- **Comment text**: `Recording` has no `comment` column either — a whole-recording free-text note
  is a `comment`-kind `Annotation` row instead (`Annotation.source == AnnotationSource.COMMENT`,
  text in `Annotation.literal`; `store/models.py`'s `Annotation`, spec
  `docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md`). A term matches via an
  EXISTS clause against `Annotation` scoped to that source, `literal ILIKE f"%{term}%"`. This
  correction was made **after** the original scope sketch (which assumed a plain `Recording.comment`
  field predating the 2026-09-23 annotations feature) — verified directly against the current
  `store/models.py` rather than carried over from that older note.
- **Site name**: `Site.name` (already used by `site:<name>` above).
- **Species name**: scientific name + English/German common names from `taxa_*.yaml`
  (`Taxon.scientific_name`/`common_name_de`/`common_name_en`), matched via the recording's current
  claims the same way `annotation:`/`species:` resolve a taxon, not a separate lookup path.

### Tokenizer (hand-rolled, two-phase — not a PEG parser)

A parser-generator library was considered (per the user's question during design) and rejected:
this grammar has no nesting and no operator precedence once OR/negation are out of scope, which is
the entire value proposition of a PEG/recursive-descent engine — it would add a dependency and a
grammar DSL without removing any of the actual work, which is the whitelist-to-SQLAlchemy
translation layer regardless of how tokens are produced. Consistent with the project's existing
dependency-averse style (no JS build step, `node:test` over jsdom, hand-rolled checks preferred
over pulling in a library for something this simply scoped — see this repo's root `CLAUDE.md`,
"Prefer local checks over external hook/CI dependencies").

A single-regex `finditer` pass over the raw string was tried first during design and rejected: a
greedy field-value pattern can consume past its intended token boundary using adjacent text as
context, which is exactly the class of bug a one-pass scan is prone to. The correct hand-rolled
shape is two phases instead:

1. **Quote-aware whitespace split** — a plain character scan (not regex): walk the string once,
   toggle an `in_quotes` flag on `"`, split on whitespace only while `in_quotes` is false. Produces
   a list of raw token strings; quoting is resolved before any field/phrase classification happens.
2. **Classify each token independently**, each pattern anchored to that single already-isolated
   token (`^...$`), so a pattern cannot overreach into a neighboring token — there's nothing after
   it to consume:
   - `^(\w+):"([^"]*)"$` → quoted field (case 1 above)
   - `^(\w+):(.+)$` → unquoted field (case 2), reclassified as a free-text word if `\w+` isn't in
     the whitelist, or (for `annotation:`/`verdict:`/`is:`) if the value isn't a member of that
     field's closed vocabulary — appends a warning to `ParsedQuery.warnings` in either case (see
     "Warnings" below)
   - `^"([^"]*)"$` → quoted phrase (case 3)
   - else → free-text word (case 4)

**Mismatched quote handling.** If phase 1 ends with `in_quotes` still `true` (an odd number of
`"` in the input), the split is unsafe — an unterminated quote at the string's end would otherwise
silently swallow every remaining token into one oversized field value (traced live during design:
`site:"Kleiner Wald bbb ccc` — unterminated — would otherwise still match the *unquoted*-field
pattern in step 2 because `(.+)` doesn't care about embedded spaces or the stray `"`, producing a
`site:` value that has silently absorbed the rest of the user's query). Instead: pre-count `"` in
the raw input before splitting; on an odd count, skip field/phrase parsing entirely and treat the
whole input as plain whitespace-split free-text words (same "unrecognized input degrades to free
text, never a parse error" shape as the unwhitelisted-field case), and appends
`Unmatched quote — showing plain-text results.` to `ParsedQuery.warnings`.

`ParsedQuery.warnings: list[str]` collects every case above plus `species:`'s DB-dependent
resolution (see "Warnings: silent divergence always surfaces" above and "Backend" below) — all
purely informational: none of them change what "degrade to free text" means, they only make the
degradation visible instead of silent (see UI section).

## Backend

`services/search.py` (new), not folded into `map_query.py`:

- `parse_search_query(text: str) -> ParsedQuery` — the tokenizer/grammar above. `ParsedQuery` is a
  frozen dataclass: `free_text_terms: list[str]`, `species_value: str | None` (the raw, unresolved
  `species:` value — resolution needs a DB session, so it happens in `search_recordings`, not
  during parsing), `annotation_source: IdSource | None`, `site_name: str | None`,
  `verdict: Verdict | None`, `is_favourite/is_flagged/is_commented: bool`, `warnings: list[str]`.
- `_resolve_species_value(session, value: str) -> int | None` — the three-representation lookup
  from "Species value resolution" above (code, then exact scientific name, then the `"G. epithet"`
  abbreviated form; ambiguous or no match → `None`).
- `search_recordings(session, parsed, *, offset=0, limit=MAX_SEARCH_RESULTS) -> SearchPage` — if
  `parsed.species_value` is set, resolves it via `_resolve_species_value` first; on `None` (no
  unique taxon), the raw value is appended to the free-text terms actually used for this query
  (not mutating the input `ParsedQuery`) and a warning appended to `SearchPage.warnings`, per
  "Warnings" above — otherwise it's passed as `filtered_recordings`'s existing `taxon_id` param,
  same as `verdict`/`favourite_only`/`source` for the fields that already exist there (`annotation:`
  and `verdict:` need no new SQL at all — see the field-whitelist table above). `filtered_recordings`
  gains one new param, `flagged_only: bool = False`, mirroring `favourite_only` exactly (a plain
  existing boolean column, `Recording.flagged_for_review`) — `is:flagged` passes it the same way
  `is:favourite` passes `favourite_only`. The genuinely new SQL lives directly in `search.py`:
  free-text `Recording.path`/`Annotation.literal` ILIKE clauses (the latter scoped to
  `Annotation.source == AnnotationSource.COMMENT`) plus a `Site`/`Taxon` name-match EXISTS, and
  `is:commented`'s EXISTS-on-`Annotation` (same source scoping as the free-text comment match).
  Each filter therefore lives in exactly one place — `map_query.py` for what it already owns
  (extended by exactly one new param), `search.py` for what's genuinely new — rather than being
  forked into two copies. `SearchPage.warnings` is
  `parsed.warnings` plus any warning raised during this resolution step, so the UI has one list to
  render regardless of which layer produced each entry.
- Pagination: Python-side slice with the same `limit(N+1)`-truncation-detection shape
  `services/sessions.py`'s `SessionsPage`/`MAX_SESSIONS` already use, not SQL `OFFSET`/`LIMIT` —
  consistent with `filtered_recordings`'s own established Python-side-filtering convention at this
  project's current scale (see the "Python, not SQL" tech-debt backlog item, which already flags
  this as a known future cost, not a new one this feature introduces).

## UI

New `/search` page (`web/views/search.py` + `search.html`), reachable via a new **Search** entry in
`_nav.html`'s sidebar, positioned right after **Map** (decided during design: search is a primary
way to find a recording, comparably fundamental to browsing the map — ahead of the more
specialized Sessions/Species/Sites/Statistics/Review entries).

Follows the established list-page shape (`.filter-bar` + `.entity-list` + pagination, same as
`sessions_list.html`):

```
┌─────────────────────────────────────────────────────┐
│ Search                                               │
├─────────────────────────────────────────────────────┤
│ [ species:PIPPIP site:"Kleiner Wald"          ] (i)  │  <- .filter-bar: text input + help button
│ Fields: species: annotation: site: verdict: is:...   │  <- inline whitelist hint
│ ⚠ Unknown field 'spceies' — searched as text instead │  <- one line per page.warnings entry
├─────────────────────────────────────────────────────┤
│ Time              │ Filename        │ Site │ Species │  <- .entity-list
│ 2026-09-14 22:05  │ PIPPIP_...wav   │ ...  │ ...     │
│ ...                                                   │
├─────────────────────────────────────────────────────┤
│ [< Newer]                                  [Older >] │  <- same pagination shape as sessions_list.html
└─────────────────────────────────────────────────────┘
```

- Single `type="search"` text input, `.filter-bar`. Debounced live-apply
  (`hx-trigger="keyup changed delay:400ms, search"`) rather than every-keystroke or a separate
  submit button — still "applies immediately, no save step" per the style guide's filter rule,
  just debounced because a partial free-text term firing a query on every keypress would be
  wasteful and visually jumpy in a way the existing dropdown/date filters never are.
- Inline hint line below the input, listing the whitelisted field names — this is genuinely new
  syntax with no other precedent in the app, so needs on-page discoverability; a user can't be
  expected to already know the grammar.
- **Warning line(s)** — one per `SearchPage.warnings` entry (unknown field, mismatched quote, an
  unrecognized `annotation:`/`verdict:`/`is:` value, or an ambiguous/unresolved `species:` value —
  every case from "Warnings: silent divergence always surfaces" above), only rendered when
  non-empty. Styled with `--color-warning` (the same token `.merge-badge` already uses), placed
  directly under the input/hint, above the results — so a user whose query silently degraded to
  free text anywhere has an explicit reason why, rather than just getting unexpectedly-broad
  results with no indication their query wasn't parsed as intended.
- Results table columns: **Time** (`local_datetime`, links to the recording), **Filename**
  (`os.path.basename(recording.path)` — `Recording` has no stored `filename` column, only `path`;
  also links to the recording — first place the app displays a filename at all; the open backlog
  item "We have to show the filename(s) somewhere" tracks the broader ask across other pages, out
  of scope here), **Site** (links to the site), **Species** (current-best headline, same wording
  `recording_headline` already produces elsewhere).
- No-result row: same `.entity-list` empty-row convention as `sessions_list.html`'s "No sessions
  match these filters."

### Syntax help

An `(i)` icon button (`icon("info-circle")`) sits directly right of the search input, inside the
same `.filter-bar` row — `type="button"`, not a submit, so it can't accidentally trigger a search.
Clicking it opens a modal with a compact explanation of the syntax; this is the page's only
on-page teaching beyond the one-line field hint already under the input (which stays, as a
without-a-click reminder of the field names for someone who's already used the modal once).

```
                    (backdrop dims the whole page)
        ┌───────────────────────────────────────┐
        │ Search syntax                      [x]│
        │                                         │
        │ Type anything to search filenames,     │
        │ comments, sites, and species names.    │
        │ Use "quotes" for an exact phrase, or   │
        │ the fields below to narrow it.         │
        │                                         │
        │ ┌─────────────────────┬───────────────┐│
        │ │ Type this           │ To find       ││
        │ ├─────────────────────┼───────────────┤│
        │ │ pipistrellus buzz   │ ...           ││
        │ │ species:PIPPIP      │ ...           ││
        │ │ ...                 │ ...           ││
        │ └─────────────────────┴───────────────┘│
        │                                         │
        │ Example: species:PIPPIP is:favourite   │
        └───────────────────────────────────────┘
```

Centered, fixed width similar to the existing comment popup (roomier — this is a table, not a
short text field — a `max-width` around 480px, `max-height: calc(100vh - 32px)` with internal
scroll, same shape the comment popup's own overflow handling already uses). Closes via the `[x]`,
Escape, or a backdrop click (see below for why backdrop-click is safe here but not for the comment
popup).

**Shared modal, not a page-specific one.** The app already has exactly one centered-modal-with-
backdrop pattern (`recording_detail.js`'s comment popup, `.comment-popup`/`.comment-popup-backdrop`
in `app.css`), built page-specific rather than as a shared class. This help modal is a second,
unrelated consumer of the same look, so per the style guide's "promote on second use" rule, this
change extracts the positioning/appearance rules (`position: fixed; inset: 0; margin: auto; height:
fit-content`, the backdrop dimming, `z-index` layering, border/shadow/background) into shared
`.modal`/`.modal-backdrop` classes, and both the comment popup and this new help modal use them.
Behavior that's specific to the comment popup — no backdrop-click-to-dismiss, because a draft could
otherwise be silently discarded by a click meant for the backdrop — stays exactly as it is and does
NOT move to the shared class; the help modal has no draft to lose, so its own JS wires a plain
backdrop-click handler that closes it, alongside Escape (both instances already close on Escape).

**Content** — a short intro line, then a table, nothing else. Written for the person using the
search box, not for a reader of this spec: no mention of tokens, SQL, `ParsedQuery`, EXISTS
clauses, or any other implementation term from the sections above.

> **Search syntax**
>
> Type anything to search filenames, comments, sites, and species names. Use `"quotes"` for an
> exact phrase, or the fields below to narrow your search. Combine several — they all have to
> match.
>
> | Type this | To find |
> |---|---|
> | `pipistrellus buzz` | Free text — matches filenames, comments, site names, or species names |
> | `"Kleiner Wald"` | An exact phrase |
> | `species:PIPPIP` | A species by its code |
> | `species:"Pipistrellus pipistrellus"` | A species by its full scientific name |
> | `species:"P. pipistrellus"` | A species by its abbreviated scientific name |
> | `annotation:bd2` | Has a classification from batdetect2 (also `bbn`, `emt`, `manual`) |
> | `site:"Kleiner Wald"` | Recordings at a specific site |
> | `verdict:species` | Identified to species (also `no_id`, `noise`, `unidentified`, `all`) |
> | `is:favourite` | Favourites only (also `is:flagged`, `is:commented`) |
>
> Example: `species:PIPPIP is:favourite` — favourite recordings of *Pipistrellus pipistrellus*.
>
> If something in your search wasn't understood (a misspelled field, a missing closing quote),
> a note appears above the results explaining what happened.

`verdict:`'s closed vocabulary (`species`/`no_id`/`noise`/`unidentified`/`all`, per the
field-whitelist table above) parses via the existing `web/params.py`'s `parse_verdict` — it already
accepts all five (see `map_query.py`'s `_passes_verdict_filter`) and already raises `ValueError` on
anything else, which `parse_search_query` catches the same way it catches an invalid
`annotation:`/`is:` value: demote to free text, append a warning.

**ILIKE escaping.** Every free-text/`site:` value reaching an `ILIKE` clause is escaped first —
`value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")`, the same escaping
`services/sessions.py`'s `filtered_sessions` already applies to its `detector` filter — otherwise a
literal `%` or `_` typed by a user is interpreted as an ILIKE wildcard instead of a literal
character (a search for `50%` would otherwise match any string with "50" followed by anything).

## Testing

- `tests/services/test_search.py` (no `db` marker): `parse_search_query` unit tests — one per
  whitelisted field (quoted and unquoted), an unwhitelisted `field:` token falling through to free
  text **and** asserting `warnings` names it, mixed free-text + field tokens, empty input, and the
  mismatched-quote fallback (asserts `warnings` is populated and every token comes back as plain
  free text, per the traced `site:"Kleiner Wald bbb ccc` case above). Also a token that's
  colon-shaped but not field-like on purpose (`12:30`) to confirm the warning is a harmless,
  non-blocking hint even on a false positive — it still searches as free text either way. Also one
  test per closed-vocabulary field with an invalid value (`annotation:foo`, `verdict:foo`,
  `is:foo`) asserting each demotes to a free-text term **and** appends a warning — the same
  "recognized field, unrecognized value" shape as the unwhitelisted-field case, per "Warnings:
  silent divergence always surfaces" above. `site:<anything>` gets no equivalent test here since
  it has no closed vocabulary to validate against.
- `db`-marked tests for `search_recordings` (needs `bats_db`/PostGIS, same tier as
  `test_map_query.py`): one test per field filter against seeded recordings, free-text matching
  each of path/filename-substring, a `comment`-kind `Annotation`'s `literal`, site name, and
  species name independently, AND-combination across multiple tokens, and the pagination
  truncation shape (`limit(N+1)`). Also `_resolve_species_value`
  specifically: all three representations resolving to the same taxon (`PIPPIP` /
  `"Pipistrellus pipistrellus"` / `"P. pipistrellus"`), case-insensitivity on the scientific-name
  and abbreviated forms, a genuinely ambiguous abbreviated form (two seeded taxa sharing a genus
  initial and epithet) resolving to `None`, and an unrecognized value resolving to `None` — and, at
  the `search_recordings` level, that an unresolved `species:` value ends up in `SearchPage`'s
  effective free-text matching (not silently dropped or forced to zero rows) with a warning in
  `SearchPage.warnings`.
- JS: no new custom JS logic (the debounce is a plain `hx-trigger` attribute) — covered by a live
  headless-Chrome check per the JS-tooling live-verification rule, confirming the debounce doesn't
  fire on every keystroke and that Enter submits immediately.

## Open questions

None — every decision surfaced during design (page vs. bolt-on, nav position, tokenizer approach,
mismatched-quote handling) is resolved above.
