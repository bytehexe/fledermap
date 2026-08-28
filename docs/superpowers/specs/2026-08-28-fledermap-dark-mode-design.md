# Fledermap Dark Mode — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing
an implementation plan.
**Date:** 2026-08-28

## Problem

Fledermap's UI (map, sessions list, session detail, nav) has no dark mode — it's always the
light palette `app.css`'s `:root` defines. The request is to add one, including whatever
`docs/style-guide.md` update the addition requires so the guide keeps describing what's actually
there (per that guide's own "match what's already there" charter — see
`docs/superpowers/specs/2026-08-27-fledermap-style-guide-design.md`).

## Goals

- Dark-mode values for every existing color token, plus one new token
  (`--color-warning`) promoted from a hardcoded hex the style guide's own rules already flag as
  a gap.
- Selection follows the OS/browser's `prefers-color-scheme` by default, with a manual
  three-state override (system → light → dark → system) in the nav, persisted across visits.
- No flash of the wrong theme on load when an override is active.
- `docs/style-guide.md` gains a "Dark mode" section: the token table grows a Dark column, plus a
  short paragraph on the mechanism, so a future page that just uses the tokens gets dark mode
  for free with nothing extra to do.

## Non-goals

- **The map itself does not theme.** Both Leaflet map instances (the main map, the
  session-detail mini-map) keep their normal light appearance in both modes — confirmed with the
  user rather than assumed, given the real alternatives (a CSS `invert()` filter on the tile
  layer, or switching to a dark tile provider) each carry a real cost (visual fidelity, or a new
  external dependency) for a page that's supposed to be a reference basemap either way.
  Consequence: Leaflet's own control chrome (zoom buttons, attribution) and the data-encoding
  marker colors (`TAXON_PALETTE`, verdict colors in `app.js`) need no changes — they're already
  correctly contrasted against a map that never changes, so this design does not touch either.
- No CSS framework, no build step, no new spacing/type scale — same "reference, not a new design
  system" charter the style guide itself already commits to. This is additive tokens plus a
  toggle, not a redesign.
- No automated visual/CSS testing. None exists in this project for any prior UI change (Phase 4
  map, the style guide pass, the map-UI motion fixes earlier this session) — verification stays
  manual, same established precedent.
- No change to Python code, routes, or the two page-specific JS files' (`app.js`,
  `session_map.js`) map/marker/drawer logic. This is CSS custom properties, one small Jinja
  partial, one Alpine component, and docs.

## Design

### 1. Token pairs

Every color in the app already routes through six `--color-*` custom properties in `app.css`'s
`:root`, confirmed by grepping the whole file and every template for hex literals and named CSS
colors: the only hex/named-color gap is `.merge-badge`'s hardcoded `color: #b7791f`; `#drawer`'s
`rgba(0, 0, 0, 0.08)` box-shadow is a separate, accepted gap, not caught by that grep because it's
a function call rather than a hex literal or named color — a subtle shadow that need not follow
the theme is not worth a token. Dark mode adds a dark-mode
value for each existing token, plus promotes that hardcoded value into a seventh token so it can
have one too — exactly the style guide's own standing rule ("never hardcode a hex color in a new
rule — use the token").

| Token | Light (existing) | Dark (new) | Use |
|---|---|---|---|
| `--color-text` | `#1a1a1a` | `#e8e8e8` | Primary body text |
| `--color-muted` | `#666` | `#9a9a9a` | Secondary/metadata text |
| `--color-border` | `#d8d8d8` | `#3a3d42` | Borders on inputs, panels, dividers |
| `--color-bg` | `#ffffff` | `#1a1a1a` | Page and control background |
| `--color-bg-subtle` | `#f7f7f8` | `#242628` | Panel/toolbar background, one step off the page background |
| `--color-accent` | `#2b6cb0` | `#5b9bd5` | Links and interactive accents |
| `--color-warning` *(new)* | `#b7791f` | `#d99a3f` | `.merge-badge`'s warning color (was hardcoded) |

Dark background is a dark gray, not pure black; dark text is off-white, not pure white — both
standard practice, reducing harsh contrast against a large fill. `--color-accent` and
`--color-warning`'s dark values are lightened from their light-mode originals — the originals
are tuned for contrast against white and read too dark against a dark background otherwise.
These are plain CSS values; nudging any of them post-implementation is a one-line change.

### 2. Mechanism

Standard pattern for "system preference by default, manual override wins, no flash of the wrong
theme":

```css
:root {
  /* light tokens, as today */
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    /* dark tokens */
  }
}

:root[data-theme="dark"] {
  /* dark tokens, again -- so an explicit override to dark wins even when
     the OS itself is set to light */
}
```

- `data-theme` is absent by default (pure system-preference behavior) and is only ever set to
  the literal string `"light"` or `"dark"` by the toggle below — never `"system"`; "system" is
  represented by the attribute's *absence*, not a third value, which is what makes the `:not()`
  guard in the media-query block work.
- A **tiny inline** `<script>` (not a separate `.js` file — inline is required so it runs before
  first paint) reads `localStorage.getItem("fledermap-theme")` and, if it's `"light"` or
  `"dark"`, sets `document.documentElement.dataset.theme` to it, synchronously, before any CSS
  paints. Factored into one small Jinja partial (`_theme_init.html`) included at the top of each
  page's `<head>`, before the `app.css` `<link>`, so the three pages that each already build
  their own `<head>` (`map.html`, `sessions_list.html`, `session_detail.html`) share one copy
  rather than three near-duplicates of the same six lines.
- The toggle itself lives in `_nav.html` (already a shared partial across all three pages) as a
  plain Alpine component — consistent with `_nav.html`'s existing collapse-toggle pattern, and
  needs no new JS file since Alpine is already loaded everywhere `_nav.html` is included.
  `x-data="{ theme: localStorage.getItem('fledermap-theme') || 'system' }"`, a button cycling
  system → light → dark → system on click, writing the new value to `localStorage` (removing the
  key entirely when it cycles back to `"system"`, not storing the literal string `"system"`) and
  syncing `document.documentElement.dataset.theme` to match (removing the attribute entirely for
  `"system"`).

### 3. Toggle placement

In `_nav.html`, near the existing `#sidebar-toggle` (☰) button — same visual family, icon-only
when the sidebar is collapsed like the nav links already are. A single button showing the
current state (🖥️ system / ☀️ light / 🌙 dark) rather than inventing new iconography or a
three-way radio control.

### 4. `docs/style-guide.md` update

- The existing "Color tokens" table gains a "Dark" column (the table in §1 above) and
  `--color-warning` as a seventh row.
- A new "Dark mode" section (placed after "Color tokens"): states the mechanism in brief (system
  preference by default, `data-theme` attribute for the override, `_theme_init.html` for the
  anti-flash init) and the consequence for future work — *"any new page or rule that uses the
  tokens gets dark mode for free; a rule that hardcodes a color instead does not, and violates
  the existing 'never hardcode a hex' rule for exactly this reason."*
- Notes the map/Leaflet non-goal explicitly, so a future contributor doesn't "fix" the map's
  light tiles as a perceived oversight.

## Testing / Verification

- No Python behavior changes: `hatch fmt --check`, `hatch run types:check`, and `hatch test` stay
  green exactly as before.
- No JS test harness exists in this repo for any prior change (`app.js`/`session_map.js` have
  zero automated coverage today) — this design doesn't introduce one for a CSS/toggle feature;
  verification is manual.
- Manual verification pass (CDP screenshots, same technique the style-guide pass used) covering,
  for each of the three pages, at least: system-light, system-dark (via
  `Emulation.setEmulatedMedia`), and both manual override states — confirming no flash on load
  for an active override, correct token application throughout, and that the map/session
  mini-map genuinely don't change.

## Open items for implementation

None outstanding in the design itself. Classified Architectural at the start (a new,
whole-app-spanning theming convention plus a persisted user preference) — that classification
doesn't downgrade because the resulting diff is CSS-token-sized; the next step after user review
is a `writing-plans` implementation plan.
