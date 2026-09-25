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
