# Fledermap Icon Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every emoji/dingbat icon in the app with inline SVG icons from Tabler Icons
(MIT), themed via the existing `--color-*` CSS variables, ending the "wrong glyph" / "no real
hollow-solid pair" drift emoji caused.

**Architecture:** A new Jinja global `icon(name, filled=False)` reads a vendored Tabler SVG file
from disk and inlines it (required for `currentColor` theming — an `<img>` can't inherit CSS
color). `vendor_assets.py` gets one pinned entry per `(icon, style)` pair actually used. Three
places that swap icons via JS (theme toggle, play/pause, view lock) move from
`textContent`/`x-text` rewriting to toggling `hidden` on pre-rendered sibling `<svg>`s, since you
can't assign SVG markup through a text-content API.

**Tech Stack:** Flask/Jinja2 (existing), Tabler Icons SVGs fetched via the existing
`vendor_assets.py` pinned-fetch mechanism, vanilla JS (existing, no new dependency).

**Spec:** `docs/superpowers/specs/2026-09-13-fledermap-icon-set-design.md`

## Global Constraints

- Icon set: **Tabler Icons**, pinned version **`3.46.0`**, MIT licensed.
- Every icon file is fetched individually via the existing `vendor_assets.py` pattern (pinned URL
  + SHA-256, verified before write) — never the whole library, never an unpinned/`@latest` URL.
- Icons are **inlined**, never `<img src="...">` — required for `currentColor` theming.
- Icon sizing: `width: 1em; height: 1em;` via a shared `.icon` CSS class, so icons scale with
  surrounding text/button font-size — Tabler's own SVG markup already carries a `class="icon ..."`
  attribute, so no markup transformation is needed to pick this rule up.
- No frontend build step, no sprite-assembly tooling — a plain file read at render time.
- Every JS-driven icon swap uses the `hidden`-attribute-toggle convention already established
  elsewhere in this codebase (`recording_detail.js`'s `scrollEl.hidden`/`tooSmallMessage.hidden`),
  never `textContent`/`innerHTML` rewriting of SVG markup.
- Live verification (headless Chrome) is **mandatory**, not optional, for every JS-driven icon
  swap (`CLAUDE.md`'s JavaScript tooling section) — check every state of a toggle, not just the
  default, and check layout/alignment, not just presence (see spec's Testing section).

---

## Task 1: Vendor asset entries for every needed icon file

**Files:**
- Modify: `src/fledermap/services/vendor_assets.py`
- Modify: `tests/test_vendor_assets.py`

**Interfaces:**
- Produces: 21 new entries in `ASSETS` (the existing `tuple[VendorAsset, ...]`), each with
  `relative_path` of the form `icons/{outline,filled}/<name>.svg`. Later tasks' `icon()` global
  reads files at exactly these paths under `<static_root>/vendor/`.

- [ ] **Step 1: Add the 21 new `VendorAsset` entries**

Append to the `ASSETS` tuple in `src/fledermap/services/vendor_assets.py`, right before the
closing `)`:

```python
    # Tabler Icons (MIT, https://tabler.io/icons), pinned 3.46.0 -- design spec
    # docs/superpowers/specs/2026-09-13-fledermap-icon-set-design.md. Fetched and hashed directly
    # against unpkg.com before this plan was written -- not invented. Only the specific
    # (icon, style) pairs this app actually uses, never the whole library.
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/flag.svg",
        sha256="eafc36e1306bc87d9ae65fd6a75c5aff61d0ceb5ff852dbf94a03fa2068a2f89",
        relative_path="icons/outline/flag.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/flag.svg",
        sha256="012892f51a80299cff80e2ac96a8e64adebb72027e7614b86839c9f098971791",
        relative_path="icons/filled/flag.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/star.svg",
        sha256="9903f4359966f48225710d78088db8fdfbc30eb82596a4bb28bc49b56399465d",
        relative_path="icons/outline/star.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/star.svg",
        sha256="50ade4fd4b67aff7eca0a9f2ad0e8c52ef4cea659be4a1fff8f03a1a9647aec7",
        relative_path="icons/filled/star.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/sun.svg",
        sha256="91c6966504af4b913d7e231804b91a9bf1bce5b42ba5bc9d08c8e52cf0ebfb0c",
        relative_path="icons/filled/sun.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/moon.svg",
        sha256="86f4dd8822aec2c6b9ea9a9286371136587ec0c748cf52d815e3c0ff934879d2",
        relative_path="icons/filled/moon.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/device-desktop.svg",
        sha256="a18c2a33a20de14bacbff9df97b7896006fb166379da84eb12c304abbd803286",
        relative_path="icons/outline/device-desktop.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/menu-2.svg",
        sha256="a7630e8fc37c090e8d665c8739ea838566db4bcf79c7710a554ace3bf4733e04",
        relative_path="icons/outline/menu-2.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/lock.svg",
        sha256="748ef36a7e3b7a4dc2b44a16576dc60123968a6c8184155015f85cfa442ce7d7",
        relative_path="icons/outline/lock.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/lock.svg",
        sha256="b8b0946430cc541c8bb8e473cab0e57eaf5d06f31fd46e5e21f7dd47b6ce355d",
        relative_path="icons/filled/lock.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/player-play.svg",
        sha256="2ba08da4e3f0d78b6a957e355c948bf8d5cd4241286aa8bb48a54d97e767929b",
        relative_path="icons/filled/player-play.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/player-pause.svg",
        sha256="bb1386fd0e04b18f46d0399610b234cf7bca059638a3d3bd3f022ad54a48ac6b",
        relative_path="icons/filled/player-pause.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/player-skip-back.svg",
        sha256="53c3b0f8e6c21ca64faac237065f721a3f2bfaaddcce6731958aa662e351a4c8",
        relative_path="icons/filled/player-skip-back.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/rotate.svg",
        sha256="27bd2a929eee1460da0948b24156194d65b0a33fb3ba356d6ee18572d4eb76b1",
        relative_path="icons/outline/rotate.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/chevron-left.svg",
        sha256="55836d95fec17c6eaafa376b1c203c79fd04baf32fe1cd528a6df6884e16e866",
        relative_path="icons/outline/chevron-left.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/chevron-right.svg",
        sha256="0142561fc2fd1b6a18b2cb0c08959b6037643aecf6adc6612aefe16ec4f39f0f",
        relative_path="icons/outline/chevron-right.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/alert-triangle.svg",
        sha256="92a951d8e90c1b1d986449e74a8b2e1c52982dc3dbe09b141ac11990420408f4",
        relative_path="icons/outline/alert-triangle.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/info-circle.svg",
        sha256="bf4377dff0aaaddf1676a4bb7292f90f1102f19daaca7021331ba6a191317c1c",
        relative_path="icons/outline/info-circle.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/check.svg",
        sha256="012d69548a769f4287e0af4c535b2cdf6e40457651de8078c4e588f0488952d6",
        relative_path="icons/outline/check.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/chevron-down.svg",
        sha256="a3a801faaeeb084e8a96897a5e2f355870b861e9606a1b50a89011b2718cc322",
        relative_path="icons/outline/chevron-down.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/x.svg",
        sha256="445e1b53563ad7e089f94dee923c2ef3444c63d45294a733687471a76a3c3b8d",
        relative_path="icons/outline/x.svg",
    ),
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_vendor_assets.py`:

```python
def test_assets_includes_every_relative_path_exactly_once() -> None:
    """A real regression risk right after appending 21 new entries by hand: a copy-paste
    duplicate relative_path would silently overwrite one icon file with another's bytes on
    fetch, with no error anywhere in this module."""
    relative_paths = [asset.relative_path for asset in ASSETS]
    assert len(relative_paths) == len(set(relative_paths))


def test_assets_includes_every_icon_the_app_uses() -> None:
    """Pins the exact (icon, style) inventory design spec 2026-09-13 requires -- catches an
    icon silently dropped from ASSETS (icon() would then raise IconNotFoundError at render
    time, but this test catches it before that ever ships)."""
    relative_paths = {asset.relative_path for asset in ASSETS}
    expected = {
        "icons/outline/flag.svg", "icons/filled/flag.svg",
        "icons/outline/star.svg", "icons/filled/star.svg",
        "icons/filled/sun.svg", "icons/filled/moon.svg",
        "icons/outline/device-desktop.svg", "icons/outline/menu-2.svg",
        "icons/outline/lock.svg", "icons/filled/lock.svg",
        "icons/filled/player-play.svg", "icons/filled/player-pause.svg",
        "icons/filled/player-skip-back.svg", "icons/outline/rotate.svg",
        "icons/outline/chevron-left.svg", "icons/outline/chevron-right.svg",
        "icons/outline/alert-triangle.svg", "icons/outline/info-circle.svg",
        "icons/outline/check.svg", "icons/outline/chevron-down.svg",
        "icons/outline/x.svg",
    }
    assert expected <= relative_paths
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `hatch test tests/test_vendor_assets.py -m "not db"`
Expected: PASS (these are pure assertions against the `ASSETS` tuple — no network call, so they
pass as soon as Step 1's entries exist).

- [ ] **Step 4: `hatch fmt` and `hatch run types:check`**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/vendor_assets.py tests/test_vendor_assets.py
git commit -m "feat: pin Tabler Icons vendor assets for the icon-set migration"
```

---

## Task 2: `icon()` Jinja global

**Files:**
- Create: `src/fledermap/web/icons.py`
- Create: `tests/test_web_icons.py`
- Modify: `src/fledermap/web/app.py`

**Interfaces:**
- Consumes: `Path` (the vendor directory, `static_root / "vendor"` — `create_app` already
  receives `static_root`, see `web/app.py:25-30`).
- Produces: `make_icon_global(vendor_dir: Path) -> Callable[[str, bool], Markup]`, registered as
  `app.jinja_env.globals["icon"]`. Every later template task calls `{{ icon("name") }}` or
  `{{ icon("name", filled=True) }}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_icons.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.web.icons import IconNotFoundError, make_icon_global


def test_icon_reads_the_outline_file_by_default(tmp_path: Path) -> None:
    (tmp_path / "icons" / "outline").mkdir(parents=True)
    (tmp_path / "icons" / "outline" / "star.svg").write_text("<svg>outline-star</svg>")

    icon = make_icon_global(tmp_path)

    assert str(icon("star")) == "<svg>outline-star</svg>"


def test_icon_reads_the_filled_file_when_requested(tmp_path: Path) -> None:
    (tmp_path / "icons" / "filled").mkdir(parents=True)
    (tmp_path / "icons" / "filled" / "star.svg").write_text("<svg>filled-star</svg>")

    icon = make_icon_global(tmp_path)

    assert str(icon("star", filled=True)) == "<svg>filled-star</svg>"


def test_icon_is_marked_safe_for_jinja_autoescape(tmp_path: Path) -> None:
    """The whole point of icon() is to inline raw SVG markup -- Jinja's autoescape would
    otherwise turn every `<` into `&lt;` and the icon would render as literal text instead of
    an image. markupsafe.Markup is what tells Jinja "this is already-safe HTML, don't escape
    it"."""
    from markupsafe import Markup

    (tmp_path / "icons" / "outline").mkdir(parents=True)
    (tmp_path / "icons" / "outline" / "star.svg").write_text("<svg>x</svg>")

    icon = make_icon_global(tmp_path)

    assert isinstance(icon("star"), Markup)


def test_icon_raises_loudly_when_the_file_is_missing(tmp_path: Path) -> None:
    """Fail loud, not silent -- same posture as this project's missing-ffmpeg/pg_dump checks
    elsewhere. A silently-empty icon would ship a visibly broken button with no error anywhere."""
    icon = make_icon_global(tmp_path)

    with pytest.raises(IconNotFoundError, match="nonexistent"):
        icon("nonexistent")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_web_icons.py -m "not db" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.web.icons'`

- [ ] **Step 3: Write the implementation**

Create `src/fledermap/web/icons.py`:

```python
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

from pathlib import Path
from typing import Callable

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_web_icons.py -m "not db" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire the global into `create_app`**

In `src/fledermap/web/app.py`, add the import near the other `fledermap.web`/`fledermap.services`
imports:

```python
from fledermap.web.icons import make_icon_global
```

And in `create_app`, right after the existing `app.jinja_env.globals[...]` lines (currently ending
at `web/app.py:55`):

```python
    app.jinja_env.globals["icon"] = make_icon_global(static_root / "vendor")
```

- [ ] **Step 6: Run the full fast test suite to confirm nothing broke**

Run: `hatch test -m "not db"`
Expected: all pass (this change is additive — no existing global/route is touched).

- [ ] **Step 7: `hatch fmt` and `hatch run types:check`**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/icons.py tests/test_web_icons.py src/fledermap/web/app.py
git commit -m "feat: add icon() Jinja global for inline Tabler SVG icons"
```

---

## Task 3: `.icon` CSS sizing/alignment rule

**Files:**
- Modify: `src/fledermap/web/static/app.css`

**Interfaces:**
- Produces: `.icon` CSS class. Every icon rendered via `{{ icon(...) }}` picks this up
  automatically (Tabler's SVG markup already includes `class="icon ..."` on its root element —
  confirmed by inspecting a fetched file in Task 1's evidence-gathering).

- [ ] **Step 1: Add the rule**

Add to `src/fledermap/web/static/app.css`, near the top-level button/typography rules (e.g. right
after the `button, .button-link { ... }` block):

```css
/* Inline Tabler SVG icons (icon() Jinja global, web/icons.py) already carry this class on their
   own root <svg> -- Tabler's fetched markup, not something this app injects. Sized to match
   surrounding text/button font-size (the same visual footprint the emoji glyphs it replaces had)
   rather than a fixed pixel size. `vertical-align` corrects the few-px-too-high default inline-
   SVG baseline (a well-known browser quirk) so it sits inline with adjacent text the way a glyph
   naturally did. `flex-shrink: 0` guards against an icon squishing inside a tight flex row (the
   same defensive pattern `.entity-header-actions`/`.panel-header-actions` already use). */
.icon {
  width: 1em;
  height: 1em;
  vertical-align: -0.125em;
  flex-shrink: 0;
}
```

- [ ] **Step 2: Commit**

```bash
git add src/fledermap/web/static/app.css
git commit -m "feat: add .icon sizing/alignment rule for inline SVG icons"
```

(No automated test for a pure CSS rule with nothing to assert against yet — Task 4 onward's live
verification is what actually exercises this.)

---

## Task 4: Flag + Favourite icons (detail buttons, drawer panel, map filter checkboxes)

**Files:**
- Modify: `src/fledermap/web/templates/_detail_flag_button.html`
- Modify: `src/fledermap/web/templates/_detail_favourite_button.html`
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `tests/test_recording_detail_view.py`
- Modify: `tests/test_map_view.py`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2).

- [ ] **Step 1: `_detail_flag_button.html`**

Replace:
```
   ⚐/⚑ (U+2690/U+2691, hollow/solid flag) rather than the old ⚑/🚩 pair:
   same hollow-vs-solid convention _detail_favourite_button.html's ☆/★
   already uses, and both are plain monochrome glyphs (unlike 🚩, a color
   emoji) so they take `color: var(--color-warning)` the same way ☆/★ take
   `--color-accent`. The old pairing had ⚑ (already solid) for the
   UNFLAGGED state, which read as flagged, and 🚩 for FLAGGED, which
   didn't match the icon's own outline-vs-solid theme (Janna, 2026-09-11).
   A real icon set (Iconoir was suggested) is a separate, larger,
   all-of-the-app decision -- not a per-icon swap -- tracked in the
   Obsidian backlog rather than done here. #}
```
with:
```
   Uses the `flag` icon from Tabler Icons (design spec
   docs/superpowers/specs/2026-09-13-fledermap-icon-set-design.md), outline for UNFLAGGED and
   filled for FLAGGED -- both take `color: var(--color-warning)` via `currentColor`, the same
   `.favourite-toggle`/`--color-accent` pattern `_detail_favourite_button.html` uses. #}
```

And replace:
```
>{{ "⚑" if recording.flagged_for_review else "⚐" }}</button>
```
with:
```
>{{ icon("flag", filled=recording.flagged_for_review) }}</button>
```

- [ ] **Step 2: `_detail_favourite_button.html`**

Replace:
```
>{{ "★" if recording.favourite else "☆" }}</button>
```
with:
```
>{{ icon("star", filled=recording.favourite) }}</button>
```

- [ ] **Step 3: `_recording_panel.html`**

Replace:
```
    >{{ "⚑" if recording.flagged_for_review else "⚐" }}</button>
```
with:
```
    >{{ icon("flag", filled=recording.flagged_for_review) }}</button>
```

And replace:
```
    >{{ "★" if recording.favourite else "☆" }}</button>
```
with:
```
    >{{ icon("star", filled=recording.favourite) }}</button>
```

- [ ] **Step 4: `map.html` filter checkboxes — icon AND matching color**

Replace:
```
      <label>
        <input type="checkbox" name="favourite_only" value="1" x-model="favourite_only">
        ★ Favourites only
      </label>
      <label>
        <input type="checkbox" name="needs_review_only" value="1" x-model="needs_review_only">
        {# ⚑ (U+2691, solid flag) -- matches the flagged state's own glyph everywhere else
           (_detail_flag_button.html, the recording-panel/site-panel flag toggles), not the old
           🚩 color emoji this checkbox was still using (Janna, 2026-09-13: "still uses the
           wrong flag emoji - should align with the filled flag emoji that we use when
           flagged"). Same convention "★ Favourites only" above already follows. #}
        ⚑ Needs review
      </label>
```
with:
```
      <label>
        <input type="checkbox" name="favourite_only" value="1" x-model="favourite_only">
        {# Colored to match `.favourite-toggle`'s own --color-accent (design spec 2026-09-13:
           "those filter checkboxes could actually even get the same colors as we usually use
           them" -- they didn't before, plain text color). #}
        <span style="color: var(--color-accent)">{{ icon("star", filled=True) }}</span> Favourites only
      </label>
      <label>
        <input type="checkbox" name="needs_review_only" value="1" x-model="needs_review_only">
        {# Colored to match `.flag-toggle`'s own --color-warning, same reasoning as above. #}
        <span style="color: var(--color-warning)">{{ icon("flag", filled=True) }}</span> Needs review
      </label>
```

- [ ] **Step 5: Update tests asserting the old glyphs**

In `tests/test_map_view.py`, replace:
```python
    assert "☆" in html
```
with:
```python
    assert 'icons-tabler-outline icon-tabler-star' in html
```
(around line 644 — the test's surrounding context/assertion label doesn't change, only the
literal string checked)

Replace:
```python
    assert "★" not in html
```
with:
```python
    assert 'icons-tabler-filled icon-tabler-star' not in html
```

Replace (around line 671):
```python
    assert "★" in html
```
with:
```python
    assert 'icons-tabler-filled icon-tabler-star' in html
```

In `tests/test_recording_detail_view.py`, apply the same `"☆"` → outline-star /
`"★"` → filled-star substitution at lines 518, 543, 596.

- [ ] **Step 6: Run the affected tests**

Run: `hatch test tests/test_map_view.py tests/test_recording_detail_view.py -m "not db"`
Expected: PASS. (Note: several of these tests are `db`-marked — also run
`hatch test tests/test_map_view.py tests/test_recording_detail_view.py` with
`dangerouslyDisableSandbox: true` to cover those.)

- [ ] **Step 7: Live-verify (headless Chrome, mandatory)**

Against a temp dev server (see `reference-headless-chrome-live-verification-technique` memory):
for a recording in both flagged/unflagged and favourited/not-favourited states, confirm (per the
spec's strengthened verification bullet):
- The correct icon (outline vs filled) renders for each state, on both the detail page and the
  drawer panel.
- Color is `--color-warning` (flag) / `--color-accent` (star) in both light and dark mode — actual
  computed `color`, not just presence in the DOM.
- Button width/height in `.entity-header-actions`/`.panel-header-actions` is unchanged from before
  this task (compare a screenshot or bounding box against the pre-task state).
- The map filter checkboxes show the same colors as their toggle-button counterparts, and their
  icon is vertically aligned with the checkbox and label text, not offset.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/templates/_detail_flag_button.html \
        src/fledermap/web/templates/_detail_favourite_button.html \
        src/fledermap/web/templates/_recording_panel.html \
        src/fledermap/web/templates/map.html \
        tests/test_recording_detail_view.py \
        tests/test_map_view.py
git commit -m "feat: migrate flag/favourite icons to Tabler, color the map filter checkboxes to match"
```

---

## Task 5: Nav — theme toggle (3-state) and hamburger menu

**Files:**
- Modify: `src/fledermap/web/templates/_nav.html`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2).

- [ ] **Step 1: Replace the hamburger**

Replace:
```
  <button type="button" id="sidebar-toggle" @click="collapsed = !collapsed" aria-label="Toggle navigation">☰</button>
```
with:
```
  <button type="button" id="sidebar-toggle" @click="collapsed = !collapsed" aria-label="Toggle navigation">{{ icon("menu-2") }}</button>
```

- [ ] **Step 2: Replace the 3-state theme icon with sibling SVGs + `x-show`**

The theme icon currently uses `x-text` to swap an emoji character based on `theme` — this can't
carry SVG markup, so it becomes three sibling icons, each shown only for its matching theme value.
Replace:
```
  <button type="button" id="theme-toggle" x-cloak @click="cycleTheme()" :aria-label="'Theme: ' + theme + '. Click to change.'">
    <span x-text="theme === 'dark' ? '🌙' : (theme === 'light' ? '☀️' : '🖥️')"></span>
  </button>
```
with:
```
  <button type="button" id="theme-toggle" x-cloak @click="cycleTheme()" :aria-label="'Theme: ' + theme + '. Click to change.'">
    <span x-show="theme === 'light'">{{ icon("sun", filled=True) }}</span>
    <span x-show="theme === 'dark'">{{ icon("moon", filled=True) }}</span>
    <span x-show="theme === 'system'">{{ icon("device-desktop") }}</span>
  </button>
```

- [ ] **Step 3: Live-verify (headless Chrome, mandatory)**

Against a temp dev server: click the theme toggle through all three states (system → light → dark
→ system), confirming at each step that exactly ONE of the three `<span>`s is visible (the other
two have `display: none` from Alpine's `x-show`), the icon matches the state, and the hamburger's
size/alignment next to it is unchanged from `5a50dfa`'s equal-width fix (this is exactly the kind
of regression a bare "is the icon present" check would miss — verify all three states, not just
whichever one happens to be active on page load).

- [ ] **Step 4: Run the full fast + db test suites**

Run: `hatch test -m "not db"` then `hatch test` (`dangerouslyDisableSandbox: true`).
Expected: all pass — no test asserts the literal emoji text here (confirmed via grep during
planning; `_nav.html` isn't in the list of test files touching these glyphs).

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/templates/_nav.html
git commit -m "feat: migrate nav hamburger + theme toggle icons to Tabler"
```

---

## Task 6: View lock toggle (JS shape change)

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/static/recording_detail.js`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2).
- Produces: `#view-lock-toggle` now contains two sibling icon `<span>`s
  (`.lock-icon-unlocked`/`.lock-icon-locked`) plus a static `<span class="lock-label">` — later
  code must never call `.textContent =` on this button again.

- [ ] **Step 1: Template — split icon and label**

Replace (currently `recording_details.html:59`):
```
      <button type="button" id="view-lock-toggle" aria-pressed="false" title="Lock the current view: no scrolling, playback stops at the right edge">🔓 Lock view</button>
```
with:
```
      {# recording_detail.js toggles `hidden` on the two icon spans -- it never rewrites
         .textContent here any more (that couldn't carry SVG markup anyway), and the label
         text is now static, never touched by JS at all. #}
      <button type="button" id="view-lock-toggle" aria-pressed="false" title="Lock the current view: no scrolling, playback stops at the right edge">
        <span class="lock-icon-unlocked">{{ icon("lock") }}</span>
        <span class="lock-icon-locked" hidden>{{ icon("lock", filled=True) }}</span>
        <span class="lock-label">Lock view</span>
      </button>
```

- [ ] **Step 2: JS — toggle `hidden` instead of rewriting textContent**

In `src/fledermap/web/static/recording_detail.js`, find the `viewLockToggle` setup (currently
around line 551-556) and replace:
```javascript
  const viewLockToggle = document.getElementById("view-lock-toggle");
  viewLockToggle.addEventListener("click", () => {
    viewLocked = !viewLocked;
    viewLockToggle.setAttribute("aria-pressed", viewLocked ? "true" : "false");
    viewLockToggle.textContent = viewLocked ? "🔒 Lock view" : "🔓 Lock view";
    scrollEl.classList.toggle("view-locked", viewLocked);
```
with:
```javascript
  const viewLockToggle = document.getElementById("view-lock-toggle");
  const lockIconUnlocked = viewLockToggle.querySelector(".lock-icon-unlocked");
  const lockIconLocked = viewLockToggle.querySelector(".lock-icon-locked");
  viewLockToggle.addEventListener("click", () => {
    viewLocked = !viewLocked;
    viewLockToggle.setAttribute("aria-pressed", viewLocked ? "true" : "false");
    lockIconUnlocked.hidden = viewLocked;
    lockIconLocked.hidden = !viewLocked;
    scrollEl.classList.toggle("view-locked", viewLocked);
```

(The rest of the click handler — `if (viewLocked) { ... } else { ... }` — is unchanged.)

- [ ] **Step 3: Run existing recording-detail-page JS/view tests**

Run: `node --test tests/js/` and `hatch test tests/test_recording_detail_view.py -m "not db"`.
Expected: both pass — no existing test asserts `textContent`/`"🔒"`/`"🔓"` on this button
(confirmed via grep during planning).

- [ ] **Step 4: Live-verify (headless Chrome, mandatory)**

Against a temp dev server: click "Lock view" and confirm (a) exactly one of the two icon spans is
visible at a time, in both directions of the toggle, (b) the label text never changes (still reads
"Lock view" locked or not — this is a deliberate behavior change from the old
"🔒 Lock view"/"🔓 Lock view" text, confirm it reads correctly either way), (c) button
width/alignment in the toolbar is unchanged from `82ca3df`'s hover-consistency fix, (d) actual
view-locking behavior (scroll prevention etc.) still works — this task only touches the icon/label
rendering, not the lock logic itself, but verify it wasn't accidentally broken.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/recording_detail.js
git commit -m "feat: migrate view-lock icon to Tabler, decouple it from the always-static label"
```

---

## Task 7: Playback controls (play/pause JS shape change, rewind icon)

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Modify: `src/fledermap/web/static/audio_controls.js`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2).
- Produces: `.playback-toggle` now contains two sibling icon `<span>`s
  (`.play-icon`/`.pause-icon`) — `audio_controls.js`'s `syncToggleIcon()` toggles `hidden` on
  them instead of rewriting `textContent`.

- [ ] **Step 1: `recording_details.html` — rewind + playback-toggle markup**

Replace (currently lines 145-146):
```
        <button type="button" class="playback-rewind" aria-label="Rewind to start">⏮</button>
        <button type="button" class="playback-toggle" aria-label="Play">▶</button>
```
with:
```
        <button type="button" class="playback-rewind" aria-label="Rewind to start">{{ icon("player-skip-back", filled=True) }}</button>
        {# audio_controls.js's syncToggleIcon() toggles `hidden` on these two spans -- it never
           rewrites .textContent here any more. #}
        <button type="button" class="playback-toggle" aria-label="Play">
          <span class="play-icon">{{ icon("player-play", filled=True) }}</span>
          <span class="pause-icon" hidden>{{ icon("player-pause", filled=True) }}</span>
        </button>
```

- [ ] **Step 2: `_recording_panel.html` — same markup shape**

Replace (currently lines 100-101):
```
    <button type="button" class="playback-rewind" aria-label="Rewind to start">⏮</button>
    <button type="button" class="playback-toggle" aria-label="Play">▶</button>
```
with:
```
    <button type="button" class="playback-rewind" aria-label="Rewind to start">{{ icon("player-skip-back", filled=True) }}</button>
    <button type="button" class="playback-toggle" aria-label="Play">
      <span class="play-icon">{{ icon("player-play", filled=True) }}</span>
      <span class="pause-icon" hidden>{{ icon("player-pause", filled=True) }}</span>
    </button>
```

- [ ] **Step 3: `audio_controls.js` — toggle `hidden` instead of rewriting textContent**

Replace `syncToggleIcon()` (currently lines 90-98):
```javascript
  function syncToggleIcon() {
    if (audioEl.paused) {
      toggleButton.textContent = "▶"; // play icon
      toggleButton.setAttribute("aria-label", "Play");
    } else {
      toggleButton.textContent = "⏸"; // pause icon
      toggleButton.setAttribute("aria-label", "Pause");
    }
  }
```
with:
```javascript
  const playIcon = toggleButton.querySelector(".play-icon");
  const pauseIcon = toggleButton.querySelector(".pause-icon");

  function syncToggleIcon() {
    if (audioEl.paused) {
      playIcon.hidden = false;
      pauseIcon.hidden = true;
      toggleButton.setAttribute("aria-label", "Play");
    } else {
      playIcon.hidden = true;
      pauseIcon.hidden = false;
      toggleButton.setAttribute("aria-label", "Pause");
    }
  }
```

(`playIcon`/`pauseIcon` are declared once, right before `syncToggleIcon`, reusing the existing
`toggleButton` reference already defined earlier in `initAudioControls` — not inside the function,
so the query only runs once per `.audio-controls` instance, matching how `toggleButton` itself is
already looked up once.)

- [ ] **Step 4: Run existing JS/view tests**

Run: `node --test tests/js/` and `hatch test tests/test_recording_detail_view.py tests/test_map_view.py -m "not db"`.
Expected: all pass — `audio_controls.js` isn't `require()`-able directly by `node:test` (it has
top-level DOM access), so its logic is covered only by headless-Chrome verification (next step),
matching this project's existing convention (`CLAUDE.md`'s JavaScript tooling section).

- [ ] **Step 5: Live-verify (headless Chrome, mandatory)**

Against a temp dev server, on both the recording-details page and the map drawer panel: click play,
confirm the pause icon replaces the play icon (and only one is visible at a time), click pause,
confirm it reverts. Check button width doesn't change between the two states (the play/pause icons
render at the same `.icon` 1em size, so this should hold, but verify — an icon-shape difference
causing an unequal rendered width would be exactly the kind of regression `82ca3df` fixed once
already for a different reason). Check rewind icon renders and is aligned with play/pause on the
same row.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html \
        src/fledermap/web/templates/_recording_panel.html \
        src/fledermap/web/static/audio_controls.js
git commit -m "feat: migrate playback control icons to Tabler"
```

---

## Task 8: HET frequency reset icon

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/templates/_recording_panel.html`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2).

- [ ] **Step 1: `recording_details.html`**

Replace (currently line 143):
```
          <button type="button" class="het-freq-reset" title="Reset to auto-tuned frequency">⟲</button>
```
with:
```
          <button type="button" class="het-freq-reset" title="Reset to auto-tuned frequency">{{ icon("rotate") }}</button>
```

- [ ] **Step 2: `_recording_panel.html`**

Replace (currently line 98):
```
      <button type="button" class="het-freq-reset" title="Reset to auto-tuned frequency">⟲</button>
```
with:
```
      <button type="button" class="het-freq-reset" title="Reset to auto-tuned frequency">{{ icon("rotate") }}</button>
```

- [ ] **Step 3: Live-verify (headless Chrome, mandatory)**

Switch to HET mode, confirm the reset button shows the icon, is clickable, and resets the
frequency input as before; confirm it's aligned with the frequency input/kHz label on the same
row (this button sits inline with text, not in its own row — check baseline alignment
specifically, the exact regression class the spec's verification section calls out).

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/templates/_recording_panel.html
git commit -m "feat: migrate HET reset icon to Tabler"
```

---

## Task 9: Back / prev / next chevrons

**Files:**
- Modify: `src/fledermap/web/templates/_entity_header.html`
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Modify: `src/fledermap/web/templates/session_detail.html`
- Modify: `src/fledermap/web/templates/sessions_list.html`
- Modify: `src/fledermap/web/templates/statistics_site.html`
- Modify: `src/fledermap/web/templates/statistics_species.html`
- Modify: `tests/test_map_view.py`
- Modify: `tests/test_entities_view.py`
- Modify: `tests/test_recording_detail_view.py`
- Modify: `tests/test_sessions_view.py`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2).

- [ ] **Step 1: `_entity_header.html`**

Replace (currently line 20):
```
<p><a href="{{ back_url }}">← {{ back_label }}</a></p>
```
with:
```
<p><a href="{{ back_url }}">{{ icon("chevron-left") }} {{ back_label }}</a></p>
```

- [ ] **Step 2: `recording_details.html`**

Replace (currently lines 30/34):
```
        >← Previous</a>
```
```
        >Next →</a>
```
with:
```
        >{{ icon("chevron-left") }} Previous</a>
```
```
        >Next {{ icon("chevron-right") }}</a>
```

- [ ] **Step 3: `_recording_panel.html`**

Replace (currently near the end, "← Previous"/"Next →" buttons):
```
    >← Previous</button>
```
```
    >Next →</button>
```
with:
```
    >{{ icon("chevron-left") }} Previous</button>
```
```
    >Next {{ icon("chevron-right") }}</button>
```

- [ ] **Step 4: `session_detail.html`**

Replace (currently line 8):
```
    <a href="/sessions">← Back to sessions</a>
```
with:
```
    <a href="/sessions">{{ icon("chevron-left") }} Back to sessions</a>
```

- [ ] **Step 5: `sessions_list.html`**

Replace (currently lines 80/88):
```
        >← Newer</button>
```
```
        >Older →</button>
```
with:
```
        >{{ icon("chevron-left") }} Newer</button>
```
```
        >Older {{ icon("chevron-right") }}</button>
```

- [ ] **Step 6: `statistics_site.html` and `statistics_species.html`**

In each (currently line 5), replace:
```
    <p><a href="...">← {{ ... }}</a></p>
```
with the same shape as Step 1 above, e.g. for `statistics_site.html`:
```
    <p><a href="/sites/{{ site.id }}">{{ icon("chevron-left") }} {{ label }}</a></p>
```
and for `statistics_species.html`:
```
    <p><a href="/species/{{ taxon.id }}">{{ icon("chevron-left") }} {{ taxon.scientific_name }}</a></p>
```

- [ ] **Step 7: Update tests asserting literal `←`/`→`**

Every one of these assertions checks for the glyph adjacent to specific words — update each to
check for the icon markup adjacent to the same word instead. Pattern used throughout:
`assert "← Previous" in html` becomes
`assert 'icons-tabler-outline icon-tabler-chevron-left' in html and "Previous" in html` (two
assertions, since the icon and text are no longer one literal contiguous string) — apply this
pattern at:
- `tests/test_map_view.py:613-614` (`"← Previous"`, `"Next →"`)
- `tests/test_entities_view.py:296,302,326,331` (`"← All species"`, `"← Back to map"`,
  `"← All sites"` ×2)
- `tests/test_recording_detail_view.py:295,320,953,989,1026,1029,1084,1090` (`"← Back to map"`,
  `"← Back to sessions"`, `"Next →"`, `"← Previous"` ×2, `"← Back to map"` ×2)
- `tests/test_sessions_view.py:219` (the regex `r"disabled[^>]*>← Newer</button>"` — update to
  `r"disabled[^>]*>\s*<span[^>]*>.*?chevron-left.*?</span>\s*Newer</button>"`, or simplify to two
  separate assertions: the `disabled[^>]*>` regex match stays, and a following plain
  `assert "icons-tabler-outline icon-tabler-chevron-left" in html` covers the icon)

Read each test's surrounding context before editing — some check for the exact word ("All
species" vs "All sites" vs "Back to map") which must still appear as its own assertion; only the
glyph-adjacency part of each assertion changes shape.

- [ ] **Step 8: Run the affected tests**

Run: `hatch test tests/test_map_view.py tests/test_entities_view.py tests/test_recording_detail_view.py tests/test_sessions_view.py -m "not db"`
then the same set with `dangerouslyDisableSandbox: true` (no `-m` filter) for the `db`-marked ones.
Expected: all pass.

- [ ] **Step 9: Live-verify (headless Chrome, mandatory)**

Spot-check at least the recording-details prev/next nav and one statistics page's back-link:
confirm the chevron is vertically aligned with the adjacent text (not offset above/below the
baseline), and that "Previous"/"Next" buttons in `recording_details.html`'s review-banner nav
remain equal width to each other where they did before (the disabled-vs-enabled pair specifically
— this area has its own history of alignment bugs per the style guide's disable-don't-hide rule
referenced in `_recording_panel.html`'s own comment).

- [ ] **Step 10: Commit**

```bash
git add src/fledermap/web/templates/_entity_header.html \
        src/fledermap/web/templates/recording_details.html \
        src/fledermap/web/templates/_recording_panel.html \
        src/fledermap/web/templates/session_detail.html \
        src/fledermap/web/templates/sessions_list.html \
        src/fledermap/web/templates/statistics_site.html \
        src/fledermap/web/templates/statistics_species.html \
        tests/test_map_view.py tests/test_entities_view.py \
        tests/test_recording_detail_view.py tests/test_sessions_view.py
git commit -m "feat: migrate back/prev/next chevrons to Tabler"
```

---

## Task 10: Warning, info, check icons

**Files:**
- Modify: `src/fledermap/web/templates/session_detail.html`
- Modify: `src/fledermap/web/templates/sessions_list.html`
- Modify: `src/fledermap/web/templates/statistics_global.html`
- Modify: `src/fledermap/web/templates/statistics_site.html`
- Modify: `src/fledermap/web/templates/statistics_species.html`
- Modify: `src/fledermap/web/templates/_classifier_box.html`
- Modify: `src/fledermap/web/static/app.css`
- Modify: `tests/test_sessions_view.py`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2).

- [ ] **Step 1: `session_detail.html` — warning + check, plus a color-consistency fix**

Replace (currently line 51):
```
        ⚠ This session may merge with session
```
with:
```
        {{ icon("alert-triangle") }} This session may merge with session
```

And, since `.merge-banner` currently has no `color:` while `sessions_list.html`'s `.merge-badge`
(same "merge proposal" concept) is `--color-warning` — a small consistency fix in the same spirit
as Task 4's filter-checkbox coloring — add a `.merge-warning-icon` wrapper:
```
        <span style="color: var(--color-warning)">{{ icon("alert-triangle") }}</span> This session may merge with session
```

Replace (currently line 42):
```
          <span class="save-confirmation">✓ Saved</span>
```
with:
```
          <span class="save-confirmation">{{ icon("check") }} Saved</span>
```

- [ ] **Step 2: `sessions_list.html`**

Replace (currently line 58):
```
            <td>{% if row.session.id in open_ids %}<a class="merge-badge" href="/sessions/{{ row.session.id }}">⚠ merge proposal</a>{% endif %}</td>
```
with:
```
            <td>{% if row.session.id in open_ids %}<a class="merge-badge" href="/sessions/{{ row.session.id }}">{{ icon("alert-triangle") }} merge proposal</a>{% endif %}</td>
```
(`.merge-badge` already sets `color: var(--color-warning)`, app.css:624 — no extra span needed
here, unlike Step 1's `.merge-banner`.)

- [ ] **Step 3: `statistics_global.html`, `statistics_site.html`, `statistics_species.html`**

In each file, every occurrence of `<summary>ⓘ</summary>` becomes
`<summary>{{ icon("info-circle") }}</summary>` — apply at every line listed in the spec's mapping
table gathering (statistics_global.html: 5 occurrences; statistics_site.html: 4; statistics_species.html: 3).

- [ ] **Step 4: `_classifier_box.html`**

Replace (currently line 62):
```
    <span class="save-confirmation" id="classifier-save-confirmation" hidden>✓ Saved</span>
```
with:
```
    <span class="save-confirmation" id="classifier-save-confirmation" hidden>{{ icon("check") }} Saved</span>
```

- [ ] **Step 5: Update `tests/test_sessions_view.py`**

Replace (currently line 583):
```python
    assert "✓ Saved" in saved_html
```
with:
```python
    assert 'icons-tabler-outline icon-tabler-check' in saved_html
    assert "Saved" in saved_html
```

- [ ] **Step 6: Run the affected tests**

Run: `hatch test tests/test_sessions_view.py -m "not db"` then with `dangerouslyDisableSandbox: true`
(no `-m` filter) for the `db`-marked ones.
Expected: all pass.

- [ ] **Step 7: Live-verify (headless Chrome, mandatory)**

Confirm on a session-detail page with a pending merge proposal: the warning icon is now
`--color-warning` colored (previously uncolored — a real visual change, confirm it looks right,
not just "different"). Confirm a statistics page's `ⓘ` tooltips still open/work as `<details>`
disclosure (this migration doesn't touch the `<details>`/`<summary>` interaction, only its icon)
and the icon is aligned with the `<summary>` text. Confirm the classifier box's "✓ Saved" flash
still triggers correctly after a save and reads clearly with the new icon.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/templates/session_detail.html \
        src/fledermap/web/templates/sessions_list.html \
        src/fledermap/web/templates/statistics_global.html \
        src/fledermap/web/templates/statistics_site.html \
        src/fledermap/web/templates/statistics_species.html \
        src/fledermap/web/templates/_classifier_box.html \
        tests/test_sessions_view.py
git commit -m "feat: migrate warning/info/check icons to Tabler, color the merge-banner warning"
```

---

## Task 11: Drawer collapse/close icons + classifier chip remove (JS-created element)

**Files:**
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `src/fledermap/web/templates/_classifier_box.html`
- Modify: `src/fledermap/web/static/classifier_box.js`

**Interfaces:**
- Consumes: `icon(name, filled=False)` (Task 2). `classifier_box.js` needs the `x` icon's raw SVG
  markup as a JS string constant (it creates the button via DOM APIs, not server-rendered Jinja),
  so this task also introduces a small "JS reads a data attribute the template renders" pattern —
  see Step 3.

- [ ] **Step 1: `map.html` — drawer collapse + close, site-filter-chip clear**

Replace (currently in `#drawer-header`):
```
        <button type="button" @click="$store.drawer.collapsed = !$store.drawer.collapsed" aria-label="Collapse">▾</button>
        <button type="button" @click="fledermapCloseDrawer()" aria-label="Close">×</button>
```
with:
```
        <button type="button" @click="$store.drawer.collapsed = !$store.drawer.collapsed" aria-label="Collapse">{{ icon("chevron-down") }}</button>
        <button type="button" @click="fledermapCloseDrawer()" aria-label="Close">{{ icon("x") }}</button>
```

Replace (site-filter-chip):
```
      <button type="button" @click="fledermapFilterBySite('')">×</button>
```
with:
```
      <button type="button" @click="fledermapFilterBySite('')">{{ icon("x") }}</button>
```

- [ ] **Step 2: `_classifier_box.html` — static chip-remove buttons**

Replace both occurrences (currently lines 36 and 43):
```
      <button type="button" class="classifier-chip-remove" aria-label="Remove {{ taxon.scientific_name }}">×</button>
```
```
      <button type="button" class="classifier-chip-remove" aria-label="Remove {{ sentinel_label }}">×</button>
```
with:
```
      <button type="button" class="classifier-chip-remove" aria-label="Remove {{ taxon.scientific_name }}">{{ icon("x") }}</button>
```
```
      <button type="button" class="classifier-chip-remove" aria-label="Remove {{ sentinel_label }}">{{ icon("x") }}</button>
```

- [ ] **Step 3: Give `classifier_box.js` the icon markup for its JS-created chips**

`classifier_box.js` builds a chip-remove button fresh via `document.createElement`, not from
server-rendered Jinja — it has no way to call `icon()` itself. Rather than hand-writing a second
copy of the `x` icon's SVG string in JS (which could drift from the vendored file's actual
content), this template renders it ONCE into a hidden template element `classifier_box.js` clones
from — the same "render once, clone for JS-created instances" idea already used elsewhere for
repeated structural markup.

Add to `_classifier_box.html`, right before the closing tag of the classifier box's root element
(find it by reading the file — it's the element the existing chips (`.classifier-chip`) are
siblings within):
```
<template id="classifier-chip-remove-icon">{{ icon("x") }}</template>
```

In `classifier_box.js`, replace (currently lines 113-117):
```javascript
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "classifier-chip-remove";
    removeButton.setAttribute("aria-label", "Remove " + entry.scientific_name);
    removeButton.textContent = "×";
```
with:
```javascript
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "classifier-chip-remove";
    removeButton.setAttribute("aria-label", "Remove " + entry.scientific_name);
    removeButton.appendChild(
      document.getElementById("classifier-chip-remove-icon").content.cloneNode(true),
    );
```

- [ ] **Step 4: Run existing tests**

Run: `node --test tests/js/` and `hatch test -m "not db"` (`classifier_box.js`'s DOM-dependent
logic isn't `node:test`-covered — same reason as `audio_controls.js` in Task 7 — so this is a
headless-Chrome-only check, next step).

- [ ] **Step 5: Live-verify (headless Chrome, mandatory)**

Confirm: drawer collapse/expand chevron renders and rotates the drawer correctly (the icon itself
doesn't rotate — only the drawer's own collapsed/expanded CSS state changes, confirm that's still
true and no unexpected rotation was introduced); drawer close (×) and site-filter-chip clear (×)
both render and work; on a recording-detail page with the classifier box, add a species suggestion
(creating a JS-driven chip) and confirm its remove (×) button renders correctly (cloned from the
`<template>`, not a raw textContent "×") and is clickable — this is the one case in the whole
migration where a template-clone bug could silently produce an EMPTY button (no icon, no visible
"×" either) since nothing would error; look carefully, not just for presence of *a* button.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/templates/map.html \
        src/fledermap/web/templates/_classifier_box.html \
        src/fledermap/web/static/classifier_box.js
git commit -m "feat: migrate drawer collapse/close and classifier-chip-remove icons to Tabler"
```

---

## Task 12: Style-guide/CLAUDE.md documentation + final sweep

**Files:**
- Modify: `docs/style-guide.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- None — documentation only. This is the plan's final gate: it must only be written once the
  sweep below is actually clean, per the spec's "Documentation" section.

- [ ] **Step 1: Full-codebase sweep for any remaining emoji/dingbat**

Run the same sweep used during the spec's own brainstorming (adjust the file list to the current
tree):
```bash
python3 -c "
import pathlib
files = list(pathlib.Path('src/fledermap/web/templates').glob('*.html')) + \
        list(pathlib.Path('src/fledermap/web/static').glob('*.js'))
for f in files:
    text = f.read_text(encoding='utf-8')
    for ch in text:
        if ord(ch) >= 0x2000 and ch not in '–—…':  # dashes/ellipsis are typography, not icons
            print(f.name, repr(ch), hex(ord(ch)))
"
```
Expected: no output (or only comment-text mentions of old glyphs as history, e.g.
`_detail_flag_button.html`'s/`map.html`'s existing comments already documenting the OLD pairing —
those are fine to leave, they're history, not live usage; confirm by eye that any hit is inside a
`{# ... #}` comment, not live markup).

If this finds a live (non-comment) glyph this plan's tasks missed, add a new task here before
proceeding to Step 2 — the documentation rule below must not ship with a known gap still open.

- [ ] **Step 2: Add the style-guide rule**

Add to `docs/style-guide.md` (placement: wherever the guide's other UI-element conventions live —
read the file first to match its existing section structure):

```markdown
### No emoji or Unicode dingbats in the UI

Every icon goes through the `icon()` Jinja global (`web/icons.py`) — inline Tabler Icons SVGs,
never a raw emoji or dingbat character typed into a template or JS string. This replaced the
app's previous mix of Unicode glyphs (design spec
`docs/superpowers/specs/2026-09-13-fledermap-icon-set-design.md`), which repeatedly drifted
(mismatched glyphs across files, no real hollow/solid pair for an arbitrary concept, inconsistent
cross-platform rendering). A JS-driven icon swap (a toggle between two states) renders both as
sibling `<svg>`s and toggles the `hidden` attribute — never rewrites `.textContent`/`.innerHTML`
with a new glyph.
```

- [ ] **Step 3: Add the CLAUDE.md pointer**

In `CLAUDE.md`'s Architecture section, in the `web/` bullet (which already documents the
`_layout.html` convention), add one sentence:

```
Icons are inline Tabler SVGs via the `icon()` Jinja global (`web/icons.py`) -- see
`docs/style-guide.md`'s "No emoji or Unicode dingbats in the UI" rule.
```

- [ ] **Step 4: Commit**

```bash
git add docs/style-guide.md CLAUDE.md
git commit -m "docs: add no-emoji-in-the-UI style guide rule, now that the sweep is clean"
```

---

## Final verification (after all 12 tasks)

- [ ] Run `hatch fmt`, `hatch run types:check`, `node --test tests/js/`, `hatch test -m "not db"`,
  and the full `hatch test` (`dangerouslyDisableSandbox: true`) — all clean.
- [ ] `hatch build -t wheel` then `python3 -m zipfile -l dist/*.whl` — confirm `web/icons.py` ships
  (it's under `src/fledermap/`, so it should by hatchling's default src-layout packaging, but
  verify per `CLAUDE.md`'s own warning about assuming this rather than checking).
- [ ] Full live-verification pass across every page type (map, drawer, recording details,
  sessions, species/sites/statistics, reviews) in both light and dark mode — the spec's
  strengthened Testing section in full, not just per-task spot checks.
- [ ] Deploy (`pipx install --force .` then `systemctl --user restart fledermap.target`) and a
  final live check against the real production instance.
- [ ] Update the Obsidian backlog's icon-set item to checked, referencing the final commit.
