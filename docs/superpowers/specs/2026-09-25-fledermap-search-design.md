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
| `annotation:<source>` | `bd2`/`bbn`/`emt`/`manual` — has a claim from that `IdSource` | new EXISTS clause in `search.py`, same `.any()` shape `map_query.py`'s `source` filter already uses |
| `site:<name>` | site name, exact | new `Site.name ==` join in `search.py` |
| `verdict:<value>` | closed `Verdict` enum | `filtered_recordings`'s existing `verdict` param |
| `is:favourite` / `is:flagged` / `is:commented` | boolean namespace, room for a future boolean without a new prefix | `favourite_only` (existing param) / new `Recording.flagged_for_review` clause / new `Recording.comment` non-empty clause |

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

Unfielded words/phrases match against `filename`, `comment`, site name, and species name
(scientific name + English/German common names from `taxa_*.yaml`) — each term AND'd against the
others, each term itself an OR across those four targets.

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
  same as `verdict`/`favourite_only` for the fields that already exist there. The genuinely new SQL
  lives directly in `search.py`: free-text ILIKE/EXISTS
  clauses, `annotation:`'s EXISTS-on-`Identification.source`, `is:flagged`'s
  `Recording.flagged_for_review`, `is:commented`'s `Recording.comment` non-empty check. Each filter
  therefore lives in exactly one place — `map_query.py` for what it already owns, `search.py` for
  what's new — rather than being forked into two copies. `SearchPage.warnings` is
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
│ [ species:PIPPIP site:"Kleiner Wald"            ]    │  <- .filter-bar, one text input
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
- Results table columns: **Time** (`local_datetime`, links to the recording), **Filename** (also
  links to the recording — first place the app displays a filename at all; the open backlog item
  "We have to show the filename(s) somewhere" tracks the broader ask across other pages, out of
  scope here), **Site** (links to the site), **Species** (current-best headline, same wording
  `recording_headline` already produces elsewhere).
- No-result row: same `.entity-list` empty-row convention as `sessions_list.html`'s "No sessions
  match these filters."

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
  each of filename/comment/site name/species name independently, AND-combination across multiple
  tokens, and the pagination truncation shape (`limit(N+1)`). Also `_resolve_species_value`
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
