"""Non-blocking reminder for pre-commit: point at the style guide when a commit
touches Fledermap UI templates or CSS.

This does not (and cannot) verify style-guide *compliance* — that needs real
judgment, not a grep, per CLAUDE.md's "Prefer local checks... reimplementation
is genuinely small" rule; checking prose conventions against markup is exactly
the "uncommon or huge" case that rule reserves for real review, not a script.
What it closes is a narrower gap: `docs/style-guide.md` and the
`fledermap-style-guide` skill are both invoked by judgment only (the skill's
own description has to be matched by whichever session is doing the work) —
nothing forces a UI change to actually consult them. This is the mechanical
backstop: it fires unconditionally whenever a template or `app.css` is staged,
independent of whether a skill got triggered, and always exits 0 so it can
never block a commit on its own.
"""

from __future__ import annotations

import sys


def main(paths: list[str]) -> int:
    if paths:
        print(
            "Reminder: this commit touches Fledermap UI templates/CSS — "
            "check docs/style-guide.md (shared classes, interaction rules, "
            "element-placement precedent) before committing.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
