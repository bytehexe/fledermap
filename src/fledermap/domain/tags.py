"""`#tag` syntax for the freeform recording `note` field -- no separate Tag entity (spec:
autocomplete/rename were explicitly out of scope, search was the only hard requirement), so a
tag is just a substring convention within the note's own text, extracted on demand.

Syntax: `#` followed by letters/digits/underscore/hyphen, optionally extended by `:` plus more of
the same (`#device:emt2`) for a lightweight key-value style. The colon is consumed only when
immediately followed by another tag character -- `#TODO: check this` must not swallow the colon
and the following prose into the tag, since that punctuation is virtually always a sentence
separator, not part of the tag someone meant to write. A `#` is also required to sit at a word
boundary (not preceded by another tag character), so `issue#42` in prose is not treated as tag
`#42`.
"""

from __future__ import annotations

import re

TAG_RE = re.compile(r"(?<![A-Za-z0-9_-])#([A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)*)")


def extract_tags(text: str) -> set[str]:
    """Every distinct tag in `text`, lowercased (matching is case-insensitive; nothing needs the
    originally-typed case back)."""
    return {match.group(1).lower() for match in TAG_RE.finditer(text)}
