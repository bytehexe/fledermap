"""Recordings search: a whitelisted field grammar plus free text (design spec
docs/superpowers/specs/2026-09-25-fledermap-search-design.md). `parse_search_query` is pure (no DB
access) -- species-value resolution needs a session and happens later, in `search_recordings`
(Task 3/4 below)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource, IdSource, Verdict, parse_verdict
from fledermap.services.map_query import filtered_recordings
from fledermap.store.models import (
    Annotation,
    Identification,
    Recording,
    Site,
    Taxon,
    TaxonCode,
)

MAX_SEARCH_RESULTS = 200

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
                    warnings.append(
                        f"Unknown verdict '{value}' — searched as text instead."
                    )
                    free_text_terms.append(raw_token)
            elif field_name == "is":
                if value == "favourite":
                    is_favourite = True
                elif value == "flagged":
                    is_flagged = True
                elif value == "commented":
                    is_commented = True
                else:
                    warnings.append(
                        f"Unknown is: value '{value}' — searched as text instead."
                    )
                    free_text_terms.append(raw_token)
            else:
                warnings.append(
                    f"Unknown field '{field_name}' — searched as text instead."
                )
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
        session.scalars(
            select(Recording.id).where(Recording.path.ilike(pattern))
        ).all(),
    )

    comment_ids = set(
        session.scalars(
            select(Annotation.recording_id).where(
                Annotation.source == AnnotationSource.COMMENT,
                Annotation.literal.ilike(pattern),
            ),
        ).all(),
    )

    site_ids = set(
        session.scalars(select(Site.id).where(Site.name.ilike(pattern))).all()
    )
    site_recording_ids = (
        set(
            session.scalars(
                select(Recording.id).where(Recording.site_id.in_(site_ids))
            ).all()
        )
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
            # filtered_recordings' own verdict=None default means "SPECIES only" (the map's
            # default view) -- search has no such default narrowing, so an unspecified
            # `verdict:` field means "all", not "only identified-as-species recordings".
            verdict=parsed.verdict if parsed.verdict is not None else "all",
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
    return SearchPage(
        rows=page_rows, has_more=total > offset + limit, warnings=warnings
    )
