#!/usr/bin/env python3
"""Commit-message check for pre-commit's `commit-msg` stage.

Replaces the external `jorisroovers/gitlint` hook. `.gitlint` only opted
into one rule beyond gitlint's stock defaults —
`contrib-title-conventional-commits` — so that is the one rule reimplemented
here, with no new dependency and no external repo cloned/executed at
commit time. gitlint's other default rules (title max length, no trailing
punctuation, blank line after subject) are not reproduced; this repo's own
history shows short, conventional, single-line subjects in practice, and
losing style-only enforcement is the accepted trade for dropping the
external dependency.
"""

from __future__ import annotations

import re
import sys

# Same type set gitlint's conventional-commits contrib rule accepts, plus an
# optional `(scope)` and an optional `!` for a breaking change — matches this
# repo's own commit history (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, ...).
_TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
_PATTERN = re.compile(rf"^({'|'.join(_TYPES)})(\([^)]+\))?!?: .+")


def check(subject: str) -> str | None:
    """Return an error message, or None if `subject` is a valid Conventional
    Commits title."""
    if not _PATTERN.match(subject):
        types = ", ".join(_TYPES)
        return (
            f"commit subject {subject!r} does not follow Conventional Commits "
            f"(expected one of: {types}, e.g. 'feat: add X')"
        )
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_commit_msg.py <commit-msg-file>", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        subject = f.readline().rstrip("\n")
    error = check(subject)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
