"""Generates the 8 moon-phase SVG icons under src/fledermap/web/static/icons/. Dev-only
tooling (see CLAUDE.md's "scripts/ never ships in the built package") -- run by hand whenever
the phase geometry needs retuning, not at build or run time. The 8 output files are the actual
checked-in asset; this script is how they were produced and how to reproduce/adjust them.

Model: a moon disc of radius R centered at (12, 12) in a 24x24 viewBox, matching Tabler's own
icon conventions (viewBox, stroke-width, linecap/linejoin) with one deliberate deviation: the
illuminated region gets a currentColor FILL (see the design spec's "Moon phase icons" section
for why a pure zero-fill outline can't tell new moon from full moon apart at all). The
illuminated region's boundary is the outer circle's near limb plus a terminator ellipse of
horizontal radius `rx = R * cos(theta)` (theta: 0 = new, pi = full) -- the standard
orthographic-projection terminator model.
"""

from __future__ import annotations

import math
from pathlib import Path

CX = CY = 12
R = 9
OUT_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "fledermap"
    / "web"
    / "static"
    / "icons"
)

HEADER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" class="icon icon-moon-phase icon-moon-phase-{name}">'
)
FOOTER = "</svg>"
DISC = f'<circle cx="{CX}" cy="{CY}" r="{R}"/>'


def _fmt(x: float) -> str:
    return f"{round(x, 4):g}"


def _illuminated_path(theta: float, waxing: bool) -> str:
    top = f"{CX},{CY - R}"
    bottom = f"{CX},{CY + R}"
    rx = R * math.cos(theta)
    if waxing:
        limb = f"M{top} A{R},{R} 0 0 1 {bottom}"
        sweep = 0 if rx >= 0 else 1
    else:
        limb = f"M{top} A{R},{R} 0 0 0 {bottom}"
        sweep = 1 if rx >= 0 else 0
    term = f"A{_fmt(abs(rx))},{R} 0 0 {sweep} {top}"
    return f'<path d="{limb} {term} Z" fill="currentColor" stroke="none"/>'


# (name, theta, waxing) -- theta/waxing is None for the two degenerate buckets (new/full),
# handled specially below.
BUCKETS: list[tuple[str, float | None, bool | None]] = [
    ("new", None, None),
    ("waxing-crescent", math.pi / 4, True),
    ("first-quarter", math.pi / 2, True),
    ("waxing-gibbous", 3 * math.pi / 4, True),
    ("full", math.pi, None),
    ("waning-gibbous", 3 * math.pi / 4, False),
    ("last-quarter", math.pi / 2, False),
    ("waning-crescent", math.pi / 4, False),
]


def render(name: str, theta: float | None, waxing: bool | None) -> str:
    header = HEADER.format(name=name)
    if theta is None:
        # new moon: disc outline only, no illuminated region.
        return f"{header}{DISC}{FOOTER}"
    if math.isclose(theta, math.pi):
        # full moon: the whole disc is illuminated -- fill the disc itself rather than
        # drawing a separate (degenerate, zero-width) path on top of it.
        return (
            f'{header}<circle cx="{CX}" cy="{CY}" r="{R}" fill="currentColor"/>{FOOTER}'
        )
    assert waxing is not None
    return f"{header}{DISC}{_illuminated_path(theta, waxing)}{FOOTER}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, theta, waxing in BUCKETS:
        svg = render(name, theta, waxing)
        path = OUT_DIR / f"moon-phase-{name}.svg"
        path.write_text(svg + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
