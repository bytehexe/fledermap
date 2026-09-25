# Fledermap Recordings Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/search` page where a user can find recordings by free text (filename, comment,
site name, species name) or a small whitelisted field grammar (`species:`, `annotation:`, `site:`,
`verdict:`, `is:`), with a plain-language help modal explaining the syntax.

**Architecture:** A new `services/search.py` owns the grammar (`parse_search_query`) and the
species-value resolver, and composes onto the existing `services/map_query.py`'s
`filtered_recordings` for every filter that already exists there (taxon, verdict, source,
favourite, plus one new symmetric `flagged_only` param), adding only the genuinely new SQL itself
(free text, `is:commented`). A new `web/views/search.py` + `search.html` follow the established
list-page shape (`.filter-bar` + `.entity-list` + pagination, same as `sessions_list.html`). The
help modal reuses a newly-extracted shared `.modal`/`.modal-backdrop` CSS pair, promoted out of the
existing page-specific comment-popup modal on the recording-detail page (its second consumer).

**Tech Stack:** Flask, SQLAlchemy, Jinja, htmx, vanilla JS (no build step), pytest (`db`-marked
tests need `bats_db`/PostGIS via testcontainers).

**Spec:** `docs/superpowers/specs/2026-09-25-fledermap-search-design.md`

## Global Constraints

- Every field's value comes from a fixed, typed whitelist — nothing free-text ever reaches SQL
  except as a bound/escaped parameter (spec "Goals").
- Any token whose field name is recognized but whose value doesn't resolve unambiguously demotes
  to a free-text word **and** appends a warning to `ParsedQuery.warnings`/`SearchPage.warnings` —
  this applies uniformly to an unknown field, a mismatched quote, an invalid `annotation:`/
  `verdict:`/`is:` value, and an ambiguous/unresolved `species:` value. `site:` is exempt (open
  vocabulary, no divergence to warn about).
- Every free-text/`site:` ILIKE value is escaped (`\` → `\\`, `%` → `\%`, `_` → `\_`) before
  reaching SQL, mirroring `services/sessions.py`'s existing `detector` filter escaping.
- `services/` code never imports from `web/` (verified: no existing `services/*.py` does)
  — `parse_verdict` moves to `domain/codes.py` so both layers can use it without inverting that.
- UI elements are disabled, never hidden; a filter applies immediately with no save step; no
  hardcoded hex colors — use `--color-*` tokens (`docs/style-guide.md`).
- `Recording` has no `filename` or `comment` column — filename is
  `os.path.basename(recording.path)`, and a whole-recording comment is a `comment`-kind
  `Annotation` row (`Annotation.source == AnnotationSource.COMMENT`, text in `Annotation.literal`).

## Review Focus

- **A search with zero free-text terms and zero recognized fields** (empty string, or whitespace
  only) — must return the same unfiltered-by-search result set `filtered_recordings()` alone
  would, not crash on an empty `free_text_terms` loop or an empty token list.
- **A quoted value containing the escape-sensitive ILIKE characters** (`species:"50%"`,
  `site:"a_b"`) — must be searched as a literal string, not interpreted as a SQL wildcard.
- **Two tokens naming the same field twice** (`verdict:species verdict:noise`) — the grammar has
  no stated precedence rule for this; the parser must not silently drop or crash on the second one
  (last-one-wins is an acceptable, deterministic choice, but it must be a chosen behavior with a
  test, not accidental).
- **A `species:` value that resolves via the code lookup to a code shared by two different
  taxa across two different code sources** (e.g. a WA code and a NABat code both spelled
  identically but pointing at different taxa) — must resolve to "ambiguous, no match" like the
  abbreviated-form ambiguity case, not silently pick whichever row the query happens to return
  first.
- **Pagination's `Older`/`Newer` links after the underlying data shrinks** (a stale link pointing
  past the new last page) — must clamp to a valid state (disabled button), not 500 or return a
  negative offset, mirroring `sessions_list.html`'s existing `max(0, offset - MAX_SESSIONS)`
  clamp.

---

## Task 1: `flagged_only` on `filtered_recordings`

**Files:**
- Modify: `src/fledermap/services/map_query.py:118-132` (`filtered_recordings` signature and body)
- Test: `tests/test_map_query.py`

**Interfaces:**
- Produces: `filtered_recordings(..., flagged_only: bool = False, ...)` — later tasks (Task 4)
  pass this through from `ParsedQuery.is_flagged`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_map_query.py`:

```python
def test_flagged_only_excludes_unflagged_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        flagged = _recording(session, audio_hash="a" * 64)
        flagged.flagged_for_review = True
        _recording(session, audio_hash="b" * 64)
        session.commit()

        results = filtered_recordings(session, flagged_only=True)

    assert [r.id for r in results] == [flagged.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_map_query.py::test_flagged_only_excludes_unflagged_recordings -v`
Expected: FAIL with `TypeError: filtered_recordings() got an unexpected keyword argument 'flagged_only'`

- [ ] **Step 3: Implement**

In `src/fledermap/services/map_query.py`, add the parameter and one `where` clause, mirroring the
existing `favourite_only` handling immediately below it:

```python
def filtered_recordings(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    taxon_id: int | Literal["unmapped"] | None = None,
    taxon_exclude: bool = False,
    verdict: Verdict | Literal["all", "unidentified"] | None = None,
    session_id: int | None = None,
    site_id: int | None = None,
    source: IdSource | Literal["emt"] | None = None,
    favourite_only: bool = False,
    flagged_only: bool = False,
    needs_review: bool = False,
) -> Sequence[Recording]:
```

and, right after the existing `if favourite_only:` block:

```python
    if flagged_only:
        stmt = stmt.where(Recording.flagged_for_review.is_(True))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_map_query.py::test_flagged_only_excludes_unflagged_recordings -v`
Expected: PASS

- [ ] **Step 5: Run the full non-db suite and mypy to confirm nothing else broke**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both pass — no existing caller passes a 13th positional arg that would now collide.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/map_query.py tests/test_map_query.py
git commit -m "$(cat <<'EOF'
feat: add flagged_only filter to filtered_recordings

Symmetric with the existing favourite_only param -- a plain boolean
column, needed by the upcoming search feature's is:flagged field.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Move `parse_verdict` to `domain/codes.py`; grammar and tokenizer

**Files:**
- Modify: `src/fledermap/domain/codes.py` (add `parse_verdict`)
- Modify: `src/fledermap/web/params.py:80-93` (remove the implementation, re-export instead)
- Create: `src/fledermap/services/search.py`
- Test: `tests/test_search_service.py`

**Interfaces:**
- Consumes: `fledermap.domain.codes.Verdict`, `fledermap.domain.codes.IdSource`.
- Produces: `fledermap.domain.codes.parse_verdict(raw: str | None) -> Verdict | Literal["all", "unidentified"] | None` (raises `ValueError` on an unrecognized string).
  `fledermap.services.search.ParsedQuery` (frozen dataclass: `free_text_terms: list[str]`,
  `species_value: str | None`, `annotation_source: IdSource | Literal["emt"] | None`,
  `site_name: str | None`, `verdict: Verdict | Literal["all", "unidentified"] | None`,
  `is_favourite: bool`, `is_flagged: bool`, `is_commented: bool`, `warnings: list[str]`).
  `fledermap.services.search.parse_search_query(text: str) -> ParsedQuery` — later tasks (3, 4)
  consume this.

- [ ] **Step 1: Move `parse_verdict`, keep every existing caller working**

In `src/fledermap/web/params.py`, remove the `parse_verdict` function body (currently lines
80-93) and replace it with a re-export:

```python
from fledermap.domain.codes import parse_verdict as parse_verdict  # noqa: F401 -- re-exported for web/views/map.py, web/api/geojson.py, and existing test imports
```

Place this import alongside `params.py`'s other top-of-file imports, not inline where the function
used to live. In `src/fledermap/domain/codes.py`, add the moved function verbatim (same body,
same docstring) near the `Verdict` class definition:

```python
from typing import Literal


def parse_verdict(raw: str | None) -> Verdict | Literal["all", "unidentified"] | None:
    """`unidentified` matches a recording with no current identification at
    all -- distinct from `no_id`, which (since the precedence rewrite in
    docs/superpowers/specs/2026-09-05-fledermap-manual-classification-design.md)
    can only ever come from a genuine MANUAL claim. Amends decision P4-9
    (2026-08-25-fledermap-phase4-map-design.md), which folded the two
    together before that distinction was possible."""
    if raw is None:
        return None
    if raw == "all":
        return "all"
    if raw == "unidentified":
        return "unidentified"
    return Verdict(raw)
```

(If `domain/codes.py` doesn't already import `Literal`, add it to its existing `typing` import
line instead of a new one.)

- [ ] **Step 2: Run the existing param/geojson/map tests to confirm the move is transparent**

Run: `hatch test tests/test_params.py tests/test_geojson_api.py -m "not db" -v`
Expected: PASS — `from fledermap.web.params import parse_verdict` in `tests/test_params.py` and
both view modules still resolves.

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 3: Commit the move on its own**

```bash
git add src/fledermap/domain/codes.py src/fledermap/web/params.py
git commit -m "$(cat <<'EOF'
refactor: move parse_verdict into domain/codes.py

services/ never imports from web/ in this codebase; the upcoming
search service needs parse_verdict's exact logic, so it moves to
where Verdict itself lives instead of duplicating it. web/params.py
re-exports it, so every existing caller (web/views/map.py,
web/api/geojson.py, tests/test_params.py) is unaffected.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Write the failing tokenizer tests**

Create `tests/test_search_service.py`:

```python
from __future__ import annotations

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.search import ParsedQuery, parse_search_query


def test_empty_query_has_no_terms_and_no_warnings() -> None:
    parsed = parse_search_query("")
    assert parsed == ParsedQuery(
        free_text_terms=[],
        species_value=None,
        annotation_source=None,
        site_name=None,
        verdict=None,
        is_favourite=False,
        is_flagged=False,
        is_commented=False,
        warnings=[],
    )


def test_plain_words_become_free_text_terms() -> None:
    parsed = parse_search_query("pipistrellus buzz")
    assert parsed.free_text_terms == ["pipistrellus", "buzz"]
    assert parsed.warnings == []


def test_quoted_phrase_becomes_one_free_text_term() -> None:
    parsed = parse_search_query('"Kleiner Wald" foo')
    assert parsed.free_text_terms == ["Kleiner Wald", "foo"]


def test_species_field_unquoted() -> None:
    parsed = parse_search_query("species:PIPPIP")
    assert parsed.species_value == "PIPPIP"
    assert parsed.free_text_terms == []


def test_species_field_quoted() -> None:
    parsed = parse_search_query('species:"Pipistrellus pipistrellus"')
    assert parsed.species_value == "Pipistrellus pipistrellus"


def test_annotation_field_bd2() -> None:
    parsed = parse_search_query("annotation:bd2")
    assert parsed.annotation_source == IdSource.BATDETECT2
    assert parsed.warnings == []


def test_annotation_field_emt_sentinel() -> None:
    parsed = parse_search_query("annotation:emt")
    assert parsed.annotation_source == "emt"


def test_annotation_field_unknown_value_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("annotation:foo")
    assert parsed.annotation_source is None
    assert parsed.free_text_terms == ["annotation:foo"]
    assert parsed.warnings == ["Unknown annotation source 'foo' — searched as text instead."]


def test_site_field_quoted() -> None:
    parsed = parse_search_query('site:"Kleiner Wald"')
    assert parsed.site_name == "Kleiner Wald"


def test_verdict_field_valid() -> None:
    parsed = parse_search_query("verdict:species")
    assert parsed.verdict == Verdict.SPECIES


def test_verdict_field_accepts_unidentified_and_all() -> None:
    assert parse_search_query("verdict:unidentified").verdict == "unidentified"
    assert parse_search_query("verdict:all").verdict == "all"


def test_verdict_field_invalid_value_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("verdict:foo")
    assert parsed.verdict is None
    assert parsed.free_text_terms == ["verdict:foo"]
    assert parsed.warnings == ["Unknown verdict 'foo' — searched as text instead."]


def test_is_favourite_flagged_commented() -> None:
    parsed = parse_search_query("is:favourite is:flagged is:commented")
    assert parsed.is_favourite is True
    assert parsed.is_flagged is True
    assert parsed.is_commented is True
    assert parsed.warnings == []


def test_is_field_invalid_value_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("is:foo")
    assert parsed.is_favourite is False
    assert parsed.free_text_terms == ["is:foo"]
    assert parsed.warnings == ["Unknown is: value 'foo' — searched as text instead."]


def test_unwhitelisted_field_name_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("spceies:PIPPIP")
    assert parsed.species_value is None
    assert parsed.free_text_terms == ["spceies:PIPPIP"]
    assert parsed.warnings == ["Unknown field 'spceies' — searched as text instead."]


def test_colon_shaped_free_text_still_just_a_word() -> None:
    """A token that looks field-shaped but whose prefix was never meant as a field (a timestamp)
    -- still gets the same harmless warning, but the search still runs as plain free text either
    way (see the spec's "heuristic nudge, not a hard error" note)."""
    parsed = parse_search_query("12:30")
    assert parsed.free_text_terms == ["12:30"]
    assert parsed.warnings == ["Unknown field '12' — searched as text instead."]


def test_mismatched_quote_falls_back_to_plain_free_text() -> None:
    parsed = parse_search_query('site:"Kleiner Wald bbb ccc')
    assert parsed.site_name is None
    assert parsed.free_text_terms == ["site:\"Kleiner", "Wald", "bbb", "ccc"]
    assert parsed.warnings == ["Unmatched quote — showing plain-text results."]


def test_aaa_field_value_bbb_tokenizes_as_three_separate_tokens() -> None:
    """Regression test for the greedy single-regex-pass bug found during design: a field token in
    the middle of free text must not consume neighboring words."""
    parsed = parse_search_query("aaa species:PIPPIP bbb")
    assert parsed.free_text_terms == ["aaa", "bbb"]
    assert parsed.species_value == "PIPPIP"


def test_mixed_free_text_and_fields() -> None:
    parsed = parse_search_query("buzz species:PIPPIP is:favourite")
    assert parsed.free_text_terms == ["buzz"]
    assert parsed.species_value == "PIPPIP"
    assert parsed.is_favourite is True


def test_repeated_field_last_one_wins() -> None:
    """The grammar has no stated precedence for a field given twice -- last-one-wins is the
    chosen, deterministic behavior (Review Focus item in the plan)."""
    parsed = parse_search_query("verdict:species verdict:noise")
    assert parsed.verdict == Verdict.NOISE
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `hatch test tests/test_search_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.services.search'`

- [ ] **Step 6: Implement the grammar**

Create `src/fledermap/services/search.py`:

```python
"""Recordings search: a whitelisted field grammar plus free text (design spec
docs/superpowers/specs/2026-09-25-fledermap-search-design.md). `parse_search_query` is pure (no DB
access) -- species-value resolution needs a session and happens later, in `search_recordings`
(Task 3/4 below)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from fledermap.domain.codes import IdSource, Verdict, parse_verdict

_ANNOTATION_SOURCES: dict[str, IdSource | Literal["emt"]] = {
    "bd2": IdSource.BATDETECT2,
    "bbn": IdSource.BATTYBIRDNET,
    "manual": IdSource.MANUAL,
    "emt": "emt",
}

_QUOTED_FIELD_RE = re.compile(r'^(\w+):"([^"]*)"$')
_UNQUOTED_FIELD_RE = re.compile(r"^(\w+):(.+)$")
_QUOTED_PHRASE_RE = re.compile(r'^"([^"]*)"$')

_UNMATCHED_QUOTE_WARNING = "Unmatched quote — showing plain-text results."


@dataclass(frozen=True)
class ParsedQuery:
    free_text_terms: list[str] = field(default_factory=list)
    species_value: str | None = None
    annotation_source: IdSource | Literal["emt"] | None = None
    site_name: str | None = None
    verdict: Verdict | Literal["all", "unidentified"] | None = None
    is_favourite: bool = False
    is_flagged: bool = False
    is_commented: bool = False
    warnings: list[str] = field(default_factory=list)


def _split_respecting_quotes(text: str) -> list[str]:
    """Whitespace-split, except while inside a `"..."` span -- a plain character scan, not a
    regex, so it can't fall prey to the greedy-overreach bug a single-pass `finditer` had during
    design (see the spec's Tokenizer section)."""
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch.isspace() and not in_quotes:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def parse_search_query(text: str) -> ParsedQuery:
    if text.count('"') % 2 != 0:
        # An unterminated quote makes the split below unsafe (see the spec's "Mismatched quote
        # handling") -- degrade the whole query to plain free text rather than risk one token
        # silently swallowing the rest of the input.
        return ParsedQuery(
            free_text_terms=text.split(),
            warnings=[_UNMATCHED_QUOTE_WARNING],
        )

    free_text_terms: list[str] = []
    species_value: str | None = None
    annotation_source: IdSource | Literal["emt"] | None = None
    site_name: str | None = None
    verdict: Verdict | Literal["all", "unidentified"] | None = None
    is_favourite = False
    is_flagged = False
    is_commented = False
    warnings: list[str] = []

    for raw_token in _split_respecting_quotes(text):
        match = _QUOTED_FIELD_RE.match(raw_token) or _UNQUOTED_FIELD_RE.match(raw_token)
        if match is not None:
            field_name, value = match.group(1), match.group(2)
            if field_name == "species":
                species_value = value
            elif field_name == "annotation":
                if value in _ANNOTATION_SOURCES:
                    annotation_source = _ANNOTATION_SOURCES[value]
                else:
                    warnings.append(
                        f"Unknown annotation source '{value}' — searched as text instead.",
                    )
                    free_text_terms.append(raw_token)
            elif field_name == "site":
                site_name = value
            elif field_name == "verdict":
                try:
                    verdict = parse_verdict(value)
                except ValueError:
                    warnings.append(f"Unknown verdict '{value}' — searched as text instead.")
                    free_text_terms.append(raw_token)
            elif field_name == "is":
                if value == "favourite":
                    is_favourite = True
                elif value == "flagged":
                    is_flagged = True
                elif value == "commented":
                    is_commented = True
                else:
                    warnings.append(f"Unknown is: value '{value}' — searched as text instead.")
                    free_text_terms.append(raw_token)
            else:
                warnings.append(f"Unknown field '{field_name}' — searched as text instead.")
                free_text_terms.append(raw_token)
            continue

        phrase_match = _QUOTED_PHRASE_RE.match(raw_token)
        if phrase_match is not None:
            free_text_terms.append(phrase_match.group(1))
            continue

        free_text_terms.append(raw_token)

    return ParsedQuery(
        free_text_terms=free_text_terms,
        species_value=species_value,
        annotation_source=annotation_source,
        site_name=site_name,
        verdict=verdict,
        is_favourite=is_favourite,
        is_flagged=is_flagged,
        is_commented=is_commented,
        warnings=warnings,
    )
```

Note on `test_colon_shaped_free_text_still_just_a_word`: `"12:30"` matches `_UNQUOTED_FIELD_RE`
as `field_name="12"`, `value="30"` — `"12"` isn't in the whitelist, so it hits the final `else`
branch, producing a warning naming `'12'`. This is the expected, documented "heuristic nudge, not
a hard error" behavior from the spec — the test pins the exact (slightly odd-looking, but
harmless) warning text so a future change notices if this shifts.

- [ ] **Step 7: Run tests to verify they pass**

Run: `hatch test tests/test_search_service.py -v`
Expected: PASS (all tests from Step 4)

- [ ] **Step 8: Run mypy and the full non-db suite**

Run: `hatch run types:check` and `hatch test -m "not db"`
Expected: both pass.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/services/search.py tests/test_search_service.py
git commit -m "$(cat <<'EOF'
feat: add search query grammar and tokenizer

Whitelisted field grammar (species/annotation/site/verdict/is) plus
free text, hand-rolled two-phase tokenizer (quote-aware split, then
per-token classification) per the search design spec. Any recognized
field with an unresolvable value demotes to free text with a warning,
the same treatment an unrecognized field name gets.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Species value resolution

**Files:**
- Modify: `src/fledermap/services/search.py` (add `_resolve_species_value`)
- Test: `tests/test_search_service.py` (new `db`-marked tests, same file, new `pytestmark` needed
  — see Step 1)

**Interfaces:**
- Consumes: `ParsedQuery.species_value` (Task 2).
- Produces: `_resolve_species_value(session: OrmSession, value: str) -> int | None` — Task 4
  consumes this inside `search_recordings`.

`tests/test_search_service.py` so far has no DB-touching tests, so it has no `pytestmark`. Since a
`db`-marked test needs `pytestmark = pytest.mark.db` at module scope (per this project's existing
convention, e.g. `tests/test_map_query.py`) and this file's tokenizer tests from Task 2 must NOT
require a database, split the db-marked tests into a second file rather than tagging the whole
module `db`.

**Files (revised):**
- Test: `tests/test_search_service_db.py` (new — `db`-marked, needs `bats_db`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_search_service_db.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.search import _resolve_species_value
from fledermap.store.models import Taxon, TaxonCode

pytestmark = pytest.mark.db


def _taxon(session: OrmSession, *, scientific_name: str) -> Taxon:
    taxon = Taxon(rank="species", scientific_name=scientific_name)
    session.add(taxon)
    session.flush()
    return taxon


def test_resolves_by_code(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.add(TaxonCode(source="wa", code="PIPPIP", taxon_id=taxon.id))
        session.commit()

        assert _resolve_species_value(session, "PIPPIP") == taxon.id


def test_resolves_by_full_scientific_name_case_insensitive(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.commit()

        assert _resolve_species_value(session, "pipistrellus PIPISTRELLUS") == taxon.id


def test_resolves_by_abbreviated_genus_form(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.commit()

        assert _resolve_species_value(session, "P. pipistrellus") == taxon.id
        assert _resolve_species_value(session, "p. PIPISTRELLUS") == taxon.id


def test_ambiguous_abbreviated_form_resolves_to_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _taxon(session, scientific_name="Pipistrellus pygmaeus")
        _taxon(session, scientific_name="Plecotus pygmaeus")
        session.commit()

        assert _resolve_species_value(session, "P. pygmaeus") is None


def test_unrecognized_value_resolves_to_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.commit()
        assert _resolve_species_value(session, "not a real species") is None


def test_code_shared_by_two_taxa_across_sources_resolves_to_none(engine: Engine) -> None:
    """Review Focus item: a code spelled identically under two different code sources, pointing
    at two different taxa, must not silently pick one."""
    with OrmSession(engine) as session:
        a = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        b = _taxon(session, scientific_name="Plecotus auritus")
        session.add(TaxonCode(source="wa", code="AMBIG", taxon_id=a.id))
        session.add(TaxonCode(source="nabat", code="AMBIG", taxon_id=b.id))
        session.commit()

        assert _resolve_species_value(session, "AMBIG") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_search_service_db.py -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_species_value'`

- [ ] **Step 3: Implement**

Add to `src/fledermap/services/search.py` (new imports at the top, function at the bottom):

```python
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Taxon, TaxonCode

_ABBREVIATED_RE = re.compile(r"^([A-Za-z])\.\s+(\S+)$")


def _resolve_species_value(session: OrmSession, value: str) -> int | None:
    """Three representations, tried in order -- see the spec's "Species value resolution"
    section. Every step is an exact match; ambiguity or no match returns None rather than
    guessing (spec: "a wrong match is worse than a missing one")."""
    code_taxon_ids = set(
        session.scalars(
            select(TaxonCode.taxon_id).where(TaxonCode.code == value),
        ).all(),
    )
    if len(code_taxon_ids) == 1:
        return code_taxon_ids.pop()
    if len(code_taxon_ids) > 1:
        return None

    scientific_match = session.scalar(
        select(Taxon.id).where(func.lower(Taxon.scientific_name) == value.lower()),
    )
    if scientific_match is not None:
        return scientific_match

    abbreviated = _ABBREVIATED_RE.match(value)
    if abbreviated is None:
        return None
    initial, epithet = abbreviated.group(1).lower(), abbreviated.group(2).lower()
    candidates = session.scalars(
        select(Taxon.id).where(
            func.lower(Taxon.scientific_name).like(f"{initial}%"),
            func.lower(Taxon.scientific_name).like(f"% {epithet}"),
        ),
    ).all()
    if len(candidates) == 1:
        return candidates[0]
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_search_service_db.py -v` (needs Docker — `dangerouslyDisableSandbox`)
Expected: PASS

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/search.py tests/test_search_service_db.py
git commit -m "$(cat <<'EOF'
feat: resolve species: values by code, scientific name, or abbreviation

Three exact-match representations (never fuzzy): a code from any
source, a full scientific name, or a "G. epithet" abbreviated form.
Zero or multiple matches (including a code ambiguous across two code
sources) resolve to None, never a guess.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `search_recordings` and pagination

**Files:**
- Modify: `src/fledermap/services/search.py` (add `SearchPage`, `search_recordings`,
  `_escape_ilike`, `_free_text_matching_ids`, `MAX_SEARCH_RESULTS`)
- Test: `tests/test_search_service_db.py`

**Interfaces:**
- Consumes: `parse_search_query` (Task 2), `_resolve_species_value` (Task 3),
  `filtered_recordings(..., flagged_only=...)` (Task 1).
- Produces: `SearchPage` (frozen dataclass: `rows: Sequence[Recording]`, `has_more: bool`,
  `warnings: list[str]`), `search_recordings(session, parsed, *, offset=0, limit=MAX_SEARCH_RESULTS) -> SearchPage` — Task 6's view consumes this.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_search_service_db.py`:

```python
from datetime import UTC, datetime

from geoalchemy2.elements import WKTElement

from fledermap.domain.codes import AnnotationSource, IdSource, Verdict
from fledermap.services.search import MAX_SEARCH_RESULTS, ParsedQuery, search_recordings
from fledermap.store.models import Annotation, Identification, Recording, Site


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    path: str,
    site_id: int | None = None,
    favourite: bool = False,
    flagged: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=path,
        recorded_at=datetime(2026, 9, 25, tzinfo=UTC),
        site_id=site_id,
        favourite=favourite,
        flagged_for_review=flagged,
    )
    session.add(r)
    session.flush()
    return r


def _empty_query(**overrides: object) -> ParsedQuery:
    return ParsedQuery(**overrides)  # type: ignore[arg-type]


def test_free_text_matches_filename(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(session, audio_hash="a" * 64, path="PIPPIP_20260914_220508.wav")
        _recording(session, audio_hash="b" * 64, path="EPTSER_20260914_220508.wav")
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["PIPPIP"]))

    assert [r.id for r in page.rows] == [target.id]


def test_free_text_matches_comment_annotation(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Annotation(
                recording_id=target.id,
                source=AnnotationSource.COMMENT,
                time_start_s=0.0,
                literal="feeding buzz here",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["buzz"]))

    assert [r.id for r in page.rows] == [target.id]


def test_free_text_matches_site_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=10.0,
            recording_count=1,
            first_at=datetime(2026, 9, 25, tzinfo=UTC),
            last_at=datetime(2026, 9, 25, tzinfo=UTC),
            name="Kleiner Wald",
        )
        session.add(site)
        session.flush()
        target = _recording(session, audio_hash="a" * 64, path="a.wav", site_id=site.id)
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["Kleiner"]))

    assert [r.id for r in page.rows] == [target.id]


def test_free_text_matches_species_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Identification(
                recording_id=target.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["pipistrellus"]))

    assert [r.id for r in page.rows] == [target.id]


def test_ilike_special_characters_are_escaped(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, path="fifty%off.wav")
        no_match = _recording(session, audio_hash="b" * 64, path="fiftyXoff.wav")
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["fifty%off"]))

    assert [r.id for r in page.rows] != [no_match.id]
    assert len(page.rows) == 1


def test_is_commented_filters_to_recordings_with_a_comment(engine: Engine) -> None:
    with OrmSession(engine) as session:
        commented = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Annotation(
                recording_id=commented.id,
                source=AnnotationSource.COMMENT,
                time_start_s=0.0,
                literal="note",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, _empty_query(is_commented=True))

    assert [r.id for r in page.rows] == [commented.id]


def test_is_flagged_reuses_filtered_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        flagged = _recording(session, audio_hash="a" * 64, path="a.wav", flagged=True)
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, _empty_query(is_flagged=True))

    assert [r.id for r in page.rows] == [flagged.id]


def test_annotation_field_reuses_filtered_recordings_source(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Identification(
                recording_id=target.id,
                source=IdSource.BATDETECT2,
                verdict=Verdict.SPECIES,
                taxon_id=None,
                raw_label="PIPPIP",
                first_seen_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(
            session,
            _empty_query(annotation_source=IdSource.BATDETECT2),
        )

    assert [r.id for r in page.rows] == [target.id]


def test_unresolved_species_value_falls_back_to_free_text_with_warning(engine: Engine) -> None:
    with OrmSession(engine) as session:
        # A recording whose *filename* happens to contain the literal unresolved species text --
        # proves the value became a free-text term rather than being dropped or forcing zero rows.
        target = _recording(session, audio_hash="a" * 64, path="totallymadeupspecies.wav")
        session.commit()

        page = search_recordings(
            session,
            ParsedQuery(species_value="totallymadeupspecies"),
        )

    assert [r.id for r in page.rows] == [target.id]
    assert page.warnings == [
        "No unique species match for 'totallymadeupspecies' — searched as text instead.",
    ]


def test_no_query_returns_everything_filtered_recordings_would(engine: Engine) -> None:
    """Review Focus item: an empty ParsedQuery (no free text, no fields) must not crash on an
    empty terms loop or empty token list, and must return the same set filtered_recordings()
    alone would."""
    with OrmSession(engine) as session:
        a = _recording(session, audio_hash="a" * 64, path="a.wav")
        b = _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, _empty_query())

    assert {r.id for r in page.rows} == {a.id, b.id}
    assert page.warnings == []


def test_offset_past_the_end_returns_no_rows_and_no_more(engine: Engine) -> None:
    """Review Focus item: a stale pagination link pointing past the current last page must not
    500 or return a negative/garbage state -- Python list slicing already makes this safe by
    construction, but that needs a test to be a guarantee rather than an accident."""
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, path="a.wav")
        session.commit()

        page = search_recordings(session, _empty_query(), offset=1000)

    assert page.rows == []
    assert page.has_more is False


def test_multiple_free_text_terms_and_with_multiple_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        for i in range(MAX_SEARCH_RESULTS + 5):
            _recording(session, audio_hash=f"{i:064x}", path=f"rec{i}.wav")
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["rec"]))

    assert len(page.rows) == MAX_SEARCH_RESULTS
    assert page.has_more is True

    with OrmSession(engine) as session:
        page2 = search_recordings(
            session,
            _empty_query(free_text_terms=["rec"]),
            offset=MAX_SEARCH_RESULTS,
        )
    assert len(page2.rows) == 5
    assert page2.has_more is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_search_service_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'search_recordings'`

- [ ] **Step 3: Implement**

Add to `src/fledermap/services/search.py`:

```python
from collections.abc import Sequence

from fledermap.domain.codes import AnnotationSource
from fledermap.services.map_query import filtered_recordings
from fledermap.store.models import Annotation, Identification, Recording, Site

MAX_SEARCH_RESULTS = 200


def _escape_ilike(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class SearchPage:
    rows: Sequence[Recording]
    has_more: bool
    warnings: list[str]


def _free_text_matching_ids(session: OrmSession, term: str) -> set[int]:
    """Every recording id where `term` matches the filename (via `path`), a comment annotation's
    text, its site's name, or a species name attached to any of its live identification claims --
    an OR across these four targets, per the spec's "Free text" section."""
    pattern = f"%{_escape_ilike(term)}%"

    path_ids = set(
        session.scalars(select(Recording.id).where(Recording.path.ilike(pattern))).all(),
    )

    comment_ids = set(
        session.scalars(
            select(Annotation.recording_id).where(
                Annotation.source == AnnotationSource.COMMENT,
                Annotation.literal.ilike(pattern),
            ),
        ).all(),
    )

    site_ids = set(session.scalars(select(Site.id).where(Site.name.ilike(pattern))).all())
    site_recording_ids = (
        set(session.scalars(select(Recording.id).where(Recording.site_id.in_(site_ids))).all())
        if site_ids
        else set()
    )

    taxon_ids = set(
        session.scalars(
            select(Taxon.id).where(
                (Taxon.scientific_name.ilike(pattern))
                | (Taxon.common_name_en.ilike(pattern))
                | (Taxon.common_name_de.ilike(pattern)),
            ),
        ).all(),
    )
    species_recording_ids = (
        set(
            session.scalars(
                select(Identification.recording_id).where(
                    Identification.taxon_id.in_(taxon_ids),
                ),
            ).all(),
        )
        if taxon_ids
        else set()
    )

    return path_ids | comment_ids | site_recording_ids | species_recording_ids


def search_recordings(
    session: OrmSession,
    parsed: ParsedQuery,
    *,
    offset: int = 0,
    limit: int = MAX_SEARCH_RESULTS,
) -> SearchPage:
    warnings = list(parsed.warnings)
    free_text_terms = list(parsed.free_text_terms)

    taxon_id: int | None = None
    if parsed.species_value is not None:
        resolved = _resolve_species_value(session, parsed.species_value)
        if resolved is None:
            warnings.append(
                f"No unique species match for '{parsed.species_value}' — "
                "searched as text instead.",
            )
            free_text_terms.append(parsed.species_value)
        else:
            taxon_id = resolved

    candidates: list[Recording] = list(
        filtered_recordings(
            session,
            taxon_id=taxon_id,
            verdict=parsed.verdict,
            source=parsed.annotation_source,
            favourite_only=parsed.is_favourite,
            flagged_only=parsed.is_flagged,
        ),
    )

    if parsed.site_name is not None:
        pattern_site_ids = set(
            session.scalars(select(Site.id).where(Site.name == parsed.site_name)).all(),
        )
        candidates = [r for r in candidates if r.site_id in pattern_site_ids]

    if parsed.is_commented:
        commented_ids = set(
            session.scalars(
                select(Annotation.recording_id).where(
                    Annotation.source == AnnotationSource.COMMENT,
                ),
            ).all(),
        )
        candidates = [r for r in candidates if r.id in commented_ids]

    for term in free_text_terms:
        matching_ids = _free_text_matching_ids(session, term)
        candidates = [r for r in candidates if r.id in matching_ids]

    total = len(candidates)
    page_rows = candidates[offset : offset + limit]
    return SearchPage(rows=page_rows, has_more=total > offset + limit, warnings=warnings)
```

`Taxon` is already imported (Task 3's `from fledermap.store.models import Taxon, TaxonCode`) — no
further import changes needed beyond the block above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_search_service_db.py -v`
Expected: PASS

- [ ] **Step 5: Run mypy and the full suite (including db) once for this task**

Run: `hatch run types:check` then `hatch test` (full suite, `dangerouslyDisableSandbox: true`,
~120s for the db-marked tests)
Expected: both pass, no regressions in `test_map_query.py` or elsewhere.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/search.py tests/test_search_service_db.py
git commit -m "$(cat <<'EOF'
feat: implement search_recordings

Composes onto filtered_recordings for taxon/verdict/annotation/
favourite/flagged, adds free-text matching (filename, comment
annotations, site name, species name) and is:commented as new SQL.
Python-side pagination slice, same limit(N+1) truncation-detection
shape as SessionsPage.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Extract shared `.modal`/`.modal-backdrop` CSS

**Files:**
- Modify: `src/fledermap/web/static/app.css:1170-1230` (comment-popup CSS block)
- Modify: `src/fledermap/web/templates/recording_details.html:219-232` (comment-popup markup)

**Interfaces:**
- Produces: `.modal`/`.modal-backdrop` CSS classes — Task 7's help modal consumes these.

- [ ] **Step 1: Read the current CSS block exactly as it is today**

Run: `sed -n '1169,1245p' src/fledermap/web/static/app.css` and confirm the class names/selectors
match what's referenced below before editing (this project's own CSS has grown incrementally; a
line-range in this plan can drift if something else touched the file first).

- [ ] **Step 2: Split the CSS into shared + comment-popup-specific rules**

In `src/fledermap/web/static/app.css`, replace the `.comment-popup-backdrop` / `.comment-popup`
block (currently defining `position: fixed; inset: 0; ...` generic positioning together with
`width: 260px` and the "no click-to-dismiss" comment) with:

```css
/* Shared centered-modal-with-backdrop look, extracted 2026-09-25 as the comment popup's second
   consumer (the search syntax help modal) arrived -- see the style guide's "promote on second
   use" rule. `.modal-backdrop` intercepts every click behind it by default; a consumer that wants
   backdrop-click-to-dismiss wires that itself (the comment popup deliberately does NOT, to avoid
   losing an in-progress draft to a stray click -- see recording_detail.js's comment). */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, var(--color-bg) 40%, transparent);
  z-index: 1001;
}
.modal-backdrop[hidden] { display: none; }

.modal {
  /* `inset: 0; margin: auto;`, not `top/left: 50%; transform: translate(-50%, -50%)` -- see the
     comment this replaced, still true: `transform` would make this element the containing block
     for its own `position: fixed` descendants, breaking `.comment-popup-species-suggestions`'s
     deliberate escape from this element's own `overflow-y: auto`. `height: fit-content` is
     required alongside `inset: 0` for the same reason documented there: without it, `top`/`bottom`
     both `0` with default `auto` height resolves to "stretch to fill the inset gap" instead of
     shrink-wrapping content. */
  position: fixed;
  inset: 0;
  margin: auto;
  height: fit-content;
  z-index: 1002;
  padding: 0.6rem;
  color: var(--color-text);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  box-shadow: 0 4px 16px color-mix(in srgb, black 30%, transparent);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 32px);
  overflow-y: auto;
}
.modal[hidden] { display: none; }

.comment-popup {
  width: 260px;
}
```

Delete the old `.comment-popup-backdrop`/`.comment-popup[hidden]` rules entirely (both are now
covered by `.modal-backdrop`/`.modal[hidden]` above) — search the file for `.comment-popup-backdrop`
and `.comment-popup[hidden]` to confirm there's exactly one definition of each before removing.

- [ ] **Step 3: Update the comment-popup markup to carry both classes**

In `src/fledermap/web/templates/recording_details.html`, change:

```html
  <div id="comment-popup-backdrop" class="comment-popup-backdrop" hidden></div>
  <div id="comment-popup" class="comment-popup" hidden>
```

to:

```html
  <div id="comment-popup-backdrop" class="modal-backdrop" hidden></div>
  <div id="comment-popup" class="modal comment-popup" hidden>
```

(`.comment-popup-backdrop` as a bare class is no longer used anywhere once this changes — confirm
with `grep -rn "comment-popup-backdrop" src/fledermap/web/` that only the `id` selector and this
one class reference remain, then leave the `id` alone since `recording_detail.js` selects by id.)

- [ ] **Step 4: Live-verify the comment popup still looks and behaves identically**

This is a pure CSS/markup refactor with no behavior change intended — verify with the project's
headless-Chrome technique (see the memory on this) against a running `fledermap serve` instance:
open a recording detail page, open the comment popup (Comment tool, click the spectrogram), confirm
it's still centered with a backdrop, still has no backdrop-click-dismiss, Escape still closes it,
and its width is still narrow (260px) rather than stretching to the new 480px-ish max-width other
consumers might use later.

- [ ] **Step 5: Run the JS test suite and the non-db Python suite**

Run: `node --test tests/js/` and `hatch test -m "not db"`
Expected: both pass — no JS test touches CSS class names directly, but this confirms nothing else
broke incidentally.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/static/app.css src/fledermap/web/templates/recording_details.html
git commit -m "$(cat <<'EOF'
refactor: extract shared .modal/.modal-backdrop CSS from comment popup

Promotes the comment popup's page-specific modal CSS into shared
classes ahead of the search syntax help modal becoming its second
consumer, per the style guide's promote-on-second-use rule. No
behavior change -- verified live against the running comment popup.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `/search` page — view, template, nav entry

**Files:**
- Create: `src/fledermap/web/views/search.py`
- Create: `src/fledermap/web/templates/search.html`
- Modify: `src/fledermap/web/templates/_nav.html`
- Modify: `src/fledermap/web/app.py` (register `search_bp`)
- Test: `tests/test_search_view.py`

**Interfaces:**
- Consumes: `search_recordings`, `parse_search_query` (Tasks 2/4), `web/params.py`'s `parse_int`,
  `current_best.py`'s `current_best_identification`/`recording_headline` (existing, same pattern
  as `web/views/sessions.py:88-99`).

- [ ] **Step 1: Write the failing view test**

Create `tests/test_search_view.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Recording
from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def test_search_page_renders_with_no_query(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search")

    assert response.status_code == 200
    assert "Search syntax" in response.get_data(as_text=True)


def test_search_page_finds_a_recording_by_filename(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="PIPPIP_20260914_220508.wav",
                recorded_at=datetime(2026, 9, 14, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search?q=PIPPIP")

    html = response.get_data(as_text=True)
    assert "PIPPIP_20260914_220508.wav" in html


def test_search_page_shows_a_warning_for_an_unknown_field(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search?q=spceies:PIPPIP")

    html = response.get_data(as_text=True)
    assert "Unknown field &#39;spceies&#39;" in html or "Unknown field 'spceies'" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_search_view.py -v`
Expected: FAIL with 404 (no `/search` route registered yet).

- [ ] **Step 3: Implement the view**

Create `src/fledermap/web/views/search.py`:

```python
"""The /search page (design spec docs/superpowers/specs/2026-09-25-fledermap-search-design.md) --
a full standalone page, not a drawer fragment, matching sessions.py's/entities.py's own precedent
for a first-class list view."""

from __future__ import annotations

import os

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.services.search import (
    MAX_SEARCH_RESULTS,
    parse_search_query,
    search_recordings,
)
from fledermap.store.models import Taxon
from fledermap.web.params import parse_int

search_bp = flask.Blueprint(
    "search",
    __name__,
    template_folder="../templates",
)


@search_bp.get("/search")
def search_page() -> flask.Response:
    q = flask.request.args.get("q", "")
    try:
        offset = max(0, parse_int(flask.request.args.get("offset")) or 0)
    except ValueError:
        return flask.make_response(("Invalid offset.", 400))

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        parsed = parse_search_query(q)
        page = search_recordings(session, parsed, offset=offset)

        rows = []
        for recording in page.rows:
            best = current_best_identification(recording)
            taxon = None
            if best is not None and not best.is_multi and best.primary.taxon_id is not None:
                taxon = session.get(Taxon, best.primary.taxon_id)
            rows.append(
                {
                    "recording": recording,
                    "filename": os.path.basename(recording.path),
                    "best": best,
                    "taxon": taxon,
                },
            )

        html = flask.render_template(
            "search.html",
            q=q,
            rows=rows,
            warnings=page.warnings,
            has_more=page.has_more,
            offset=offset,
            prev_offset=max(0, offset - MAX_SEARCH_RESULTS),
            next_offset=offset + MAX_SEARCH_RESULTS,
        )
    return flask.make_response(html)
```

- [ ] **Step 4: Register the blueprint**

In `src/fledermap/web/app.py`, add the import alongside the other view blueprint imports:

```python
from fledermap.web.views.search import search_bp
```

and register it alongside the others (order doesn't matter — match the existing style of one
`app.register_blueprint(...)` per line):

```python
    app.register_blueprint(search_bp)
```

- [ ] **Step 5: Add the nav entry**

In `src/fledermap/web/templates/_nav.html`, insert a new link right after the Map link:

```html
  <a class="sidebar-link" href="/"><span class="label">Map</span></a>
  <a class="sidebar-link" href="/search"><span class="label">Search</span></a>
  <a class="sidebar-link" href="/sessions"><span class="label">Sessions</span></a>
```

- [ ] **Step 6: Create the template**

Create `src/fledermap/web/templates/search.html`, following `sessions_list.html`'s shape (a
`.filter-bar` with the query input, a results wrapper with `.entity-list` + pagination, a `Species`
column reusing `recording_headline`):

```html
{% extends "_layout.html" %}
{% block title %}Fledermap — Search{% endblock %}
{% block content %}
  <main class="main-content">
    <h1>Search</h1>
    <form
      id="search-filters"
      class="filter-bar"
      method="get"
      action="/search"
      hx-get="/search"
      hx-trigger="keyup changed delay:400ms, search"
      hx-target="#search-results-wrapper"
      hx-select="#search-results-wrapper"
      hx-push-url="true"
    >
      <input type="search" name="q" value="{{ q }}" placeholder='species:PIPPIP site:"Kleiner Wald"' autocomplete="off">
      <button type="button" id="search-help-btn" aria-label="Search syntax help">{{ icon("info-circle") }}</button>
    </form>
    <p class="search-field-hint">Fields: species: annotation: site: verdict: is:favourite/flagged/commented</p>
    {% if warnings %}
    <ul class="search-warnings">
      {% for warning in warnings %}
      <li>{{ icon("alert-triangle") }} {{ warning }}</li>
      {% endfor %}
    </ul>
    {% endif %}
    <div id="search-results-wrapper">
      <table class="entity-list">
        <thead>
          <tr><th>Time</th><th>Filename</th><th>Site</th><th>Species</th></tr>
        </thead>
        <tbody>
          {% for row in rows %}
          <tr>
            <td><a href="/recordings/{{ row.recording.audio_hash }}">{{ row.recording.recorded_at | local_datetime }}</a></td>
            <td><a href="/recordings/{{ row.recording.audio_hash }}">{{ row.filename }}</a></td>
            <td>{% if row.recording.site_id %}<a href="/sites/{{ row.recording.site_id }}">Site {{ row.recording.site_id }}</a>{% endif %}</td>
            <td>{{ recording_headline(row.taxon, row.best) }}</td>
          </tr>
          {% else %}
          <tr><td colspan="4">No recordings match this search.</td></tr>
          {% endfor %}
        </tbody>
      </table>
      <div class="pagination" id="search-pagination">
        <button
          type="button"
          {% if offset == 0 %}disabled{% endif %}
          hx-get="/search?q={{ q | urlencode }}&offset={{ prev_offset }}"
          hx-target="#search-results-wrapper"
          hx-select="#search-results-wrapper"
          hx-push-url="true"
        >{{ icon("chevron-left") }} Newer</button>
        <button
          type="button"
          {% if not has_more %}disabled{% endif %}
          hx-get="/search?q={{ q | urlencode }}&offset={{ next_offset }}"
          hx-target="#search-results-wrapper"
          hx-select="#search-results-wrapper"
          hx-push-url="true"
        >Older {{ icon("chevron-right") }}</button>
      </div>
    </div>
  </main>
{% endblock %}
{% block scripts %}
  <script src="{{ url_for('vendor.static', filename='htmx.min.js') }}"></script>
{% endblock %}
```

`recording_headline` is already registered as a Jinja global (`web/app.py:154`,
`app.jinja_env.globals["recording_headline"] = recording_headline`) — `search.html` can call it
directly, no `app.py` change needed for this.

- [ ] **Step 7: Run the view tests**

Run: `hatch test tests/test_search_view.py -v`
Expected: PASS

- [ ] **Step 8: Run mypy and the full suite**

Run: `hatch run types:check` then `hatch test`
Expected: both pass.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/views/search.py src/fledermap/web/templates/search.html \
        src/fledermap/web/templates/_nav.html src/fledermap/web/app.py \
        tests/test_search_view.py
git commit -m "$(cat <<'EOF'
feat: add the /search page

New Search nav entry right after Map. Follows the established
filter-bar + entity-list + pagination list-page shape. Warnings from
SearchPage render above the results when the query didn't parse
exactly as typed.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Syntax help modal

**Files:**
- Modify: `src/fledermap/web/templates/search.html` (add the modal markup)
- Create: `src/fledermap/web/static/search.js`
- Modify: `src/fledermap/web/static/app.css` (warning/hint line styling)

**Interfaces:**
- Consumes: `.modal`/`.modal-backdrop` (Task 5).

- [ ] **Step 1: Add the modal markup to `search.html`**

Right before `{% endblock %}` of the `content` block (after the closing `</div>` of
`#search-results-wrapper`, still inside `<main>`... actually place it as a sibling of `<main>`,
matching where `recording_details.html` places its own modal markup — outside `<main>` but inside
the `content` block):

```html
  </main>
  <div id="search-help-backdrop" class="modal-backdrop" hidden></div>
  <div id="search-help-modal" class="modal search-help-modal" hidden role="dialog" aria-label="Search syntax">
    <div class="search-help-header">
      <h2>Search syntax</h2>
      <button type="button" id="search-help-close" aria-label="Close">{{ icon("x") }}</button>
    </div>
    <p>Type anything to search filenames, comments, sites, and species names. Use "quotes" for an exact phrase, or the fields below to narrow your search. Combine several — they all have to match.</p>
    <table class="entity-list">
      <thead><tr><th>Type this</th><th>To find</th></tr></thead>
      <tbody>
        <tr><td><code>pipistrellus buzz</code></td><td>Free text — matches filenames, comments, site names, or species names</td></tr>
        <tr><td><code>"Kleiner Wald"</code></td><td>An exact phrase</td></tr>
        <tr><td><code>species:PIPPIP</code></td><td>A species by its code</td></tr>
        <tr><td><code>species:"Pipistrellus pipistrellus"</code></td><td>A species by its full scientific name</td></tr>
        <tr><td><code>species:"P. pipistrellus"</code></td><td>A species by its abbreviated scientific name</td></tr>
        <tr><td><code>annotation:bd2</code></td><td>Has a classification from batdetect2 (also <code>bbn</code>, <code>emt</code>, <code>manual</code>)</td></tr>
        <tr><td><code>site:"Kleiner Wald"</code></td><td>Recordings at a specific site</td></tr>
        <tr><td><code>verdict:species</code></td><td>Identified to species (also <code>no_id</code>, <code>noise</code>, <code>unidentified</code>, <code>all</code>)</td></tr>
        <tr><td><code>is:favourite</code></td><td>Favourites only (also <code>is:flagged</code>, <code>is:commented</code>)</td></tr>
      </tbody>
    </table>
    <p>Example: <code>species:PIPPIP is:favourite</code> — favourite recordings of <em>Pipistrellus pipistrellus</em>.</p>
    <p>If something in your search wasn't understood (a misspelled field, a missing closing quote), a note appears above the results explaining what happened.</p>
  </div>
```

- [ ] **Step 2: Add the JS wiring**

Create `src/fledermap/web/static/search.js`:

```javascript
document.addEventListener("DOMContentLoaded", () => {
  const helpBtn = document.getElementById("search-help-btn");
  const modal = document.getElementById("search-help-modal");
  const backdrop = document.getElementById("search-help-backdrop");
  const closeBtn = document.getElementById("search-help-close");
  if (!helpBtn || !modal || !backdrop || !closeBtn) return;

  function closeHelp() {
    modal.hidden = true;
    backdrop.hidden = true;
    document.removeEventListener("keydown", onKeydown);
  }

  function onKeydown(event) {
    if (event.key === "Escape") closeHelp();
  }

  function openHelp() {
    modal.hidden = false;
    backdrop.hidden = false;
    document.addEventListener("keydown", onKeydown);
  }

  helpBtn.addEventListener("click", openHelp);
  closeBtn.addEventListener("click", closeHelp);
  // Unlike the comment popup's backdrop (recording_detail.js), this modal holds no
  // in-progress draft that a stray click could discard -- backdrop-click-to-dismiss is safe here.
  backdrop.addEventListener("click", closeHelp);
});

if (typeof module !== "undefined" && module.exports) {
  module.exports = {};
}
```

Add the script tag to `search.html`'s `scripts` block, after `htmx.min.js`:

```html
  <script src="{{ url_for('static', filename='search.js') }}"></script>
```

- [ ] **Step 3: Add warning/hint line CSS**

In `src/fledermap/web/static/app.css`, add (near `.merge-badge`'s existing `--color-warning`
usage, for locality):

```css
.search-field-hint {
  color: var(--color-muted);
  font-size: 0.85rem;
}
.search-warnings {
  list-style: none;
  padding: 0;
  margin: 0.4rem 0;
  color: var(--color-warning);
  font-size: 0.85rem;
}
.search-help-modal {
  max-width: 480px;
}
.search-help-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
```

- [ ] **Step 4: Live-verify with headless Chrome**

Per the JS-tooling live-verification rule (mandatory for any JS/UI change): start `fledermap serve`
against a populated dev DB (or the sample data), navigate to `/search`, and confirm:
- The `(i)` button opens the modal; the modal shows the intro text and the full table.
- Escape closes it; clicking outside the modal (on the backdrop) closes it; the `[x]` button
  closes it.
- Typing in the search box doesn't fire a request on every keystroke (network tab: no request
  until ~400ms after the last keystroke, or on pressing Enter).
- A search like `spceies:PIPPIP` shows the warning line above the results.
- A search like `species:"P. pipistrellus"` (seeded with real data, or any seeded taxon) returns
  the expected recording.

- [ ] **Step 5: Run the JS test suite and full Python suite**

Run: `node --test tests/js/` (no new file added here needs its own `node:test` coverage — `search.js`
has top-level `document.addEventListener`, so per this project's own JS-tooling convention it isn't
`require()`-able directly; its logic is simple enough that the live-verification pass above is the
right coverage, matching how `recording_detail.js`'s own comment-popup open/close functions are
verified) and `hatch test`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/templates/search.html src/fledermap/web/static/search.js \
        src/fledermap/web/static/app.css
git commit -m "$(cat <<'EOF'
feat: add search syntax help modal

An (i) button next to the search input opens a plain-language syntax
table, reusing the shared .modal/.modal-backdrop pair extracted from
the comment popup. Unlike that popup, this modal has no draft to
lose, so backdrop-click-to-dismiss is wired alongside Escape.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Final check

- [ ] Run the full suite once more end to end: `hatch test` (full, incl. `-m db`,
  `dangerouslyDisableSandbox: true`), `hatch run types:check`, `hatch fmt`, `node --test tests/js/`.
- [ ] `hatch build -t wheel` + `python3 -m zipfile -l dist/*.whl` — confirm `search.py`/`search.html`/
  `search.js` all ship in the built wheel (per CLAUDE.md's "verify with a real build, not by
  inspecting the source tree" rule for anything runtime-relevant).
