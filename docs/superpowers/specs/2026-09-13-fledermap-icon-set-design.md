# Fledermap Icon Set — Design

**Status:** draft
**Date:** 2026-09-13

## Problem

The app currently uses Unicode emoji/dingbat glyphs as UI icons throughout — flag-for-review
(⚐/⚑), favourite (☆/★), the theme toggle (🖥️/🌙/☀️), the nav hamburger (☰), view lock (🔓/🔒),
playback controls (▶/⏸/⏮), the HET-frequency reset (⟲), back/prev/next navigation (←/→), warning
(⚠), info tooltips (ⓘ), and a save-confirmation checkmark (✓). This has repeatedly caused two
kinds of drift, both found live:

- **Wrong-glyph drift**: the map filter's "Needs review" checkbox kept the old 🚩 (a color emoji)
  after `_detail_flag_button.html` moved to the hollow/solid ⚐/⚑ pair (2026-09-11), because nothing
  ties duplicated glyph literals across files together (fixed ad hoc, 2026-09-13, `93ec4cd`).
- **No real hollow/solid coverage**: emoji don't reliably offer a matched outline/filled pair for
  an arbitrary concept, which is exactly what forced the ⚐/⚑ pairing in the first place — and
  still leaves inconsistent rendering across platforms/fonts (a genuine cross-browser risk emoji
  glyphs carry that a self-hosted icon set doesn't).

Janna's explicit ruling (2026-09-11, restated 2026-09-13): adopting a real icon set is
**all-or-nothing** — every glyph in the app, not a per-icon swap — because a partial migration
just adds a second visual language alongside the first.

## Goals

- Replace every icon-shaped glyph in the app (see the mapping table below) with SVG icons from one
  consistently-licensed set, inlined so they can be themed via the existing `--color-*` CSS custom
  properties the same way the emoji-based flag/star icons already are (`color: var(--color-warning)`
  / `var(--color-accent)`).
- Preserve the existing hollow/solid (outline/filled) convention for stateful toggles (flag,
  favourite, lock, theme) — the whole reason the ⚐/⚑ swap happened in the first place.
- Fold in two fixes found during this brainstorm: the map filter's "Needs review"/"Favourites
  only" checkbox labels get colored to match their toggle-button counterparts (`--color-warning`/
  `--color-accent`), which they don't today (plain text color).
- Keep the "no frontend build step" convention: no new tooling, no sprite-assembly step, no
  npm/node build. Icons are fetched as individual pinned files, the same way every other vendor
  asset already is.

## Non-goals

- The Leaflet marker pin icons (raster PNGs from the `leaflet`/`leaflet.markercluster` vendor
  assets) — a separate system, out of scope.
- `favicon.png` / the new nav logo (`5a50dfa`) — a custom brand mark (bat-in-a-map-pin), not a
  semantic UI icon. Stays exactly as-is.
- Any future icon concept not in the mapping table below — this spec covers the app's current
  inventory; a new feature adding a new icon later picks from the same set and follows the same
  pattern, but doesn't need this spec re-opened.

## Library choice

**Tabler Icons** (`@tabler/icons`, MIT, pinned `3.46.0`), not Iconoir (the candidate named in the
original backlog item). Evidence, checked against this app's actual glyph inventory via the
GitHub repo trees of both projects (not the marketing site, which can undersell/oversell coverage):

| Concept | Iconoir | Tabler |
|---|---|---|
| Flag (pennant shape) | `triangle-flag` (outline only, **no solid** — confirmed via repo tree, contradicting an early guess that `white-flag-solid` would work; that's a different glyph, a drooping surrender flag, not a pennant) | `flag` outline **and** filled |
| Lock / unlock | `lock` (outline only, no solid; no `lock-open`/`unlock` icon exists at all) | `lock` outline **and** filled (locked = filled, unlocked = outline — no separate "open" glyph needed) |
| Theme (light/dark/system) | no plain `sun`/`moon`/`monitor` icons suited to a 3-state toggle | `sun`, `moon` (outline+filled each), `device-desktop` (outline+filled) — full 3-state coverage |
| Star (favourite) | `star` outline **and** solid | `star` outline **and** filled |
| Everything else (play/pause/skip-back/check/alert-triangle/info-circle/menu) | partial | outline **and** filled for every one |

Tabler covers every concept this app needs with a consistent outline/filled convention; Iconoir
has real, confirmed gaps beyond the one glyph originally investigated. Individual SVGs are
fetchable from unpkg the same way every other vendor asset is
(`https://unpkg.com/@tabler/icons@3.46.0/icons/{outline,filled}/<name>.svg`), confirmed live.

## Delivery mechanism

Tabler's SVGs use `stroke="currentColor"` (outline style) / `fill="currentColor"` (filled style) —
the same mechanism the existing ⚐/⚑/☆/★ emoji already rely on via `color: var(--color-warning)`
etc. This only works if the SVG is **inlined** into the page's DOM; an `<img src="...">` renders
the file as an opaque raster/vector that can't inherit `currentColor`, which would silently break
dark-mode theming for every icon.

- **`vendor_assets.py`** gets one new `VendorAsset` entry per `(icon-name, style)` pair actually
  used by the mapping table below — not the whole library. Same pattern every other vendor asset
  already follows: pinned URL, SHA-256 verified before write, lands under
  `<static_root>/vendor/icons/{outline,filled}/<name>.svg`.
- **A new Jinja global**, `icon(name, filled=False)`, reads the vendored file from disk at render
  time and returns it `Markup()`-wrapped for inline embedding. Lives alongside the other Flask app
  setup (`web/app.py` or a new small `web/icons.py`, implementer's call) — a plain file read, no
  new dependency, no caching complexity beyond what the OS page cache already gives a small file
  read on every request (matches this app's existing "no premature caching" posture elsewhere).
  Missing file (an icon fetched under the wrong name, or `ensure_vendor_assets` not yet run) should
  raise loudly at render time, not silently render nothing — same "fail loud" posture
  `ffmpeg`/`pg_dump` missing already gets elsewhere in this project, so a broken deploy is caught
  immediately rather than shipping a page with holes in it.
- **Sizing**: icons render at `width: 1em; height: 1em` via a shared `.icon` class on the inlined
  `<svg>` root, so they scale with the surrounding button/label `font-size` and sit inline with
  text — the same visual footprint the emoji glyphs have today. No per-icon size overrides unless
  a specific spot needs one (e.g. the nav logo, which is out of scope here anyway).

## JS-driven icon swaps need a shape change

Three places currently swap an emoji via `.textContent`/Alpine `x-text` — none of these work once
the icon is inline SVG markup, since you can't assign SVG through a text-content API. All three
move to the same pattern: **render both/all states as sibling inline `<svg>` elements in the
initial HTML, toggle which one is visible** (via the `hidden` attribute, or Alpine `x-show` where
Alpine already owns the state) instead of rewriting content:

- **Theme toggle** (`_nav.html`): currently `<span x-text="theme === 'dark' ? '🌙' : ...">`.
  Becomes three sibling `<svg>`s (sun/moon/device-desktop), each `x-show="theme === '...'"` —
  Alpine's own declarative toggle, no new JS needed.
- **Play/pause** (`audio_controls.js`): currently `toggleButton.textContent = "▶" / "⏸"`. Becomes
  two sibling `<svg>`s inside the button, JS toggles `.hidden` on each instead of rewriting
  `textContent` — same `hidden`-toggle convention already used throughout this codebase (e.g.
  `recording_detail.js`'s `scrollEl.hidden`/`tooSmallMessage.hidden`, added 2026-09-13).
- **View lock** (`recording_detail.js`): currently `viewLockToggle.textContent = viewLocked ? "🔒 Lock view" : "🔓 Lock view"` — couples the icon AND the label text into one assignment. Splits
  into: two sibling `<svg>`s (locked/unlocked) toggled via `hidden`, plus a separate, never-
  rewritten `<span>` holding the static "Lock view" label — a small cleanup alongside the icon
  swap, decoupling icon state from label text that never actually changes.

A fourth, distinct case: **`classifier_box.js`'s species-chip remove button** is JS-*created*
(a fresh `<button>` built via DOM APIs per chip, not a static element JS *toggles*), currently
`removeButton.textContent = "×"`. No two-state toggle needed here — just inject the `x` icon's
SVG markup once at creation time instead of the emoji character.

## Icon mapping table

| Concept | States | Tabler icon | Color | Used in |
|---|---|---|---|---|
| Flag for review | unflagged / flagged | `flag` outline / filled | `--color-warning` | `_detail_flag_button.html`, `_recording_panel.html`, map filter checkbox (`map.html`, newly colored) |
| Favourite | not / favourited | `star` outline / filled | `--color-accent` | `_detail_favourite_button.html`, `_recording_panel.html`, map filter checkbox (`map.html`, newly colored) |
| Theme toggle | light / dark / system | `sun` filled / `moon` filled / `device-desktop` outline | inherit (nav icon color) | `_nav.html` |
| Menu (hamburger) | single | `menu-2` outline | inherit | `_nav.html` |
| View lock | unlocked / locked | `lock` outline / filled | inherit | `recording_details.html` |
| Play / Pause | single each | `player-play` filled / `player-pause` filled | inherit | `audio_controls.js`-driven buttons (`recording_details.html`, `_recording_panel.html`) |
| Rewind to start | single | `player-skip-back` filled | inherit | same audio-controls markup |
| HET freq reset | single | `rotate` outline | inherit | same audio-controls markup |
| Back / prev / next | single | `chevron-left` / `chevron-right` outline | inherit | `_entity_header.html`, `session_detail.html`, `sessions_list.html`, `statistics_site.html`, `statistics_species.html`, `_recording_panel.html`, `recording_details.html` |
| Warning | single | `alert-triangle` outline | `--color-warning` (already the case) | `session_detail.html`, `sessions_list.html` |
| Info tooltip | single | `info-circle` outline | inherit | `statistics_global.html`, `statistics_site.html`, `statistics_species.html` |
| Save confirmation check | single | `check` outline | inherit | `_classifier_box.html`, `session_detail.html` |
| Drawer collapse/expand | single | `chevron-down` outline | inherit | `map.html` `#drawer-header` |
| Close / remove (×) | single | `x` outline | inherit | `map.html` (drawer close, site-filter-chip clear), `_classifier_box.html` (species-chip remove — this one is JS-*created*, not JS-*toggled*: `classifier_box.js` builds the button fresh via DOM APIs and currently sets `.textContent = "×"`; the fix there is injecting the icon's SVG markup at creation time, not the hidden-siblings toggle pattern the theme/play-pause/lock cases below need — a different, simpler case worth keeping distinct in the plan) |

## Testing / verification

- No new Python logic beyond the `icon()` Jinja global and the `vendor_assets.py` additions — both
  get ordinary unit tests (global renders expected markup for a known icon; a missing file raises).
- Every template change is markup-only; existing view tests that assert on visible text (e.g.
  button `aria-label`s, which don't change) keep passing. Any test asserting literal emoji text in
  rendered HTML needs updating to the new markup shape — grep for the glyphs listed in the mapping
  table across `tests/` before starting the implementation plan, per this project's "grep for
  every reader before planning a removal" convention.
- The three JS-driven swaps (theme, play/pause, lock) are exactly the kind of DOM-dependent logic
  `CLAUDE.md`'s JavaScript tooling section flags as **mandatory** headless-Chrome
  live-verification, not optional — confirm each state's correct icon is visible/hidden and that
  toggling still works, the same technique used throughout this session's fixes.
- Visual live-check (screenshot or headless Chrome) of at least one page from each icon's usage
  list, both light and dark mode, to confirm `currentColor` theming actually works end-to-end
  (not just that the SVG is present in the DOM).

## Documentation

Once the migration ships, add a rule to `docs/style-guide.md` (and a short pointer in
`CLAUDE.md`'s Architecture section on `web/`, matching how the `_layout.html` convention is
documented there): **no emoji or Unicode dingbats in the UI** — every icon goes through the
`icon()` Jinja global (or the inline-SVG-sibling-toggle pattern for JS-driven state) added by this
migration, never a raw glyph typed into a template or JS string. This is exactly the
"when adding a style-guide rule because an existing page violated it, sweep the other templates
for the same shape" case `CLAUDE.md`'s UI consistency process already describes — except here the
sweep *is* this entire migration, so the rule can be added with a clean codebase already behind
it, rather than the usual sweep-after-the-fact.

## Rollout

One migration, done in full per Janna's all-or-nothing ruling — the implementation plan sequences
it into reviewable steps (e.g. vendor/infra first, then one concept group at a time), but nothing
ships half-migrated to production; the two icon "languages" (emoji + new SVGs) shouldn't coexist
in a deployed build. The style-guide/CLAUDE.md update (above) lands in the same final step, once
the sweep is actually complete and true.
