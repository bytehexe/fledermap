"""Inline SVG icon lookup for Jinja templates (design spec
docs/superpowers/specs/2026-09-13-fledermap-icon-set-design.md).

Reads a vendored Tabler Icons SVG file from disk and returns it as-is for inline embedding.
Inlining (never `<img src="...">`) is required for the icon to inherit `currentColor` from its
containing element's CSS `color` -- how every themed icon (flag/star/warning/etc.) picks up the
right `--color-*` token in both light and dark mode; an `<img>`'s rendered content is opaque to
CSS color, which would silently break theming for every icon.

Tabler's own SVG markup already carries everything a caller needs: a `class="icon icon-tabler
..."` attribute (this app's `.icon { width: 1em; height: 1em; }` CSS rule, app.css, sizes it to
match surrounding text) and `icon-tabler-<name>`/`icons-tabler-{outline,filled}` classes that
double as a stable test/CSS hook -- no attribute injection needed, unlike a hand-rolled icon
pipeline might require.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from markupsafe import Markup


class IconNotFoundError(Exception):
    """Raised when `icon()` is asked for a name/style pair that wasn't fetched as a vendor
    asset -- fails loudly at render time rather than silently rendering nothing, matching this
    project's existing "missing ffmpeg/pg_dump fails loud" posture (CLAUDE.md's Environment
    gotchas section)."""


def make_icon_global(vendor_dir: Path) -> Callable[..., Markup]:
    """`vendor_dir` is `static_root / "vendor"` -- the same directory
    `services/vendor_assets.py` fetches into. Returns the function to register as the `icon`
    Jinja global; `create_app` is the only caller, since that's the only place `static_root` is
    known."""

    def icon(name: str, filled: bool = False) -> Markup:
        style = "filled" if filled else "outline"
        path = vendor_dir / "icons" / style / f"{name}.svg"
        if not path.exists():
            msg = (
                f"icon {name!r} (style={style!r}) not found at {path} -- "
                "is it in vendor_assets.py's ASSETS?"
            )
            raise IconNotFoundError(msg)
        return Markup(path.read_text(encoding="utf-8"))

    return icon
