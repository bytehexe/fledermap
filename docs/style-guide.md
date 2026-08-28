# Fledermap Style Guide

This documents the UI conventions already established by `app.css` and `map.html` — the
project's oldest and most-worked page — so later pages (`sessions_list.html`,
`session_detail.html`, and whatever comes after) match instead of drifting into inconsistent,
unstyled markup. It is a reference for matching what's already there, not a new design system:
no new spacing/type scale, no CSS framework, no build step. See
`docs/superpowers/specs/2026-08-27-fledermap-style-guide-design.md` for the reasoning behind
that scope.

## Color tokens

Defined in `app.css`'s `:root`:

| Token | Light | Dark | Use |
|---|---|---|---|
| `--color-text` | `#1a1a1a` | `#e8e8e8` | Primary body text |
| `--color-muted` | `#666` | `#9a9a9a` | Secondary/metadata text — labels, captions, timestamps |
| `--color-border` | `#d8d8d8` | `#3a3d42` | Borders on inputs, panels, dividers |
| `--color-bg` | `#ffffff` | `#1a1a1a` | Page and control background |
| `--color-bg-subtle` | `#f7f7f8` | `#242628` | Panel/toolbar background, one step off the page background |
| `--color-accent` | `#2b6cb0` | `#5b9bd5` | Links and interactive accents |
| `--color-warning` | `#b7791f` | `#d99a3f` | `.merge-badge`'s warning color |

Never hardcode a hex color in a new rule — use the token. If a new color is genuinely needed,
add it to `:root` and document it here in the same change.

## Dark mode

System preference by default (`prefers-color-scheme: dark`), with a three-state manual override
(system → light → dark → system) via the 🖥️/☀️/🌙 button in the sidebar (`_nav.html`), persisted
in `localStorage` under the key `fledermap-theme`. The override is applied via a `data-theme`
attribute on `<html>`, set by a small inline script (`_theme_init.html`, included first in every
page's `<head>`) *before* first paint, so a returning visitor with an active override never sees
a flash of the wrong theme.

Any rule that uses the tokens above gets dark mode for free — nothing extra to do. A rule that
hardcodes a color instead does not, and violates the "never hardcode a hex" rule above for
exactly this reason: `--color-warning` was promoted from `.merge-badge`'s hardcoded hex
specifically so it could have a dark counterpart.

**The map does not theme.** Both Leaflet map instances (the main map, the session-detail
mini-map) keep their normal light appearance in every mode, deliberately — see
`docs/superpowers/specs/2026-08-28-fledermap-dark-mode-design.md`'s Non-goals. Don't "fix" the
map's light tiles as a perceived oversight.

## Spacing rhythm

Not an enforced scale — match the range already in use:

- **Container padding:** `0.5rem`–`1rem` (e.g. `#sidebar { padding: 0.75rem 0.5rem; }`,
  `.filter-bar { padding: 0.6rem 1rem; }`)
- **Internal gaps** (flex `gap`, spacing between sibling controls): `0.4rem`–`0.75rem`
- **Tight label-to-control spacing:** `0.25rem`–`0.6rem` (e.g. `.stacked-form label { margin-bottom: 0.6rem; }`)

## Form controls

`.filter-bar select, .filter-bar input, .stacked-form select, .stacked-form textarea` gives
these controls this look — it's scoped to those two classes, not global, so a control outside
either renders unstyled browser-default chrome. `.filter-bar` and `.stacked-form` each also
layer their own layout rules (padding/gap for the toolbar row; display/width/margin/font-weight
for the stacked layout) on top of this shared block via the normal cascade — this is the
"promote on second use" rule in action: `.stacked-form` was the second consumer to need this
look, so the shared block grew to cover it instead of being duplicated under `.stacked-form`'s
own selector:

```css
border: 1px solid var(--color-border);
border-radius: 4px;
padding: 0.3rem 0.5rem;
background: var(--color-bg);
color: var(--color-text);
font: inherit;
font-size: 0.9rem;
```

`button` is the one form control styled globally (`app.css`'s bare `button` rule, plus
`:hover`/`:disabled` states) — any `<button>` anywhere in the app gets this automatically, no
class needed:

```css
font-size: 0.85rem;
padding: 0.35rem 0.75rem;
border: 1px solid var(--color-border);
border-radius: 4px;
background: var(--color-bg);
color: var(--color-text);
cursor: pointer;
```

## Shared classes

The two documented here are the ones this guide has promoted so far under the "promote on second
use" rule below — not an exhaustive list of every class used on more than one element.

### `.filter-bar`

Use for any horizontal, wrapping toolbar of filter controls (dropdowns, date inputs,
checkboxes) that applies a query — typically live, on `change`. Gives the form a subtle
background, bottom border, padding, and bordered inputs/selects. Used by `map.html`'s `#filters`
and `sessions_list.html`'s `#session-filters`.

```html
<form id="my-filters" class="filter-bar">
  <label>From <input type="date" name="from"></label>
  ...
</form>
```

### `.stacked-form`

Use for any form whose fields should stack top-to-bottom (label above its control) rather than
flow inline — the default for a bare `<label>Text <input></label>` is inline, which sprawls
across a wide column. Used by `session_detail.html`'s edit form and merge-resolution form.

```html
<form class="stacked-form">
  <label>Note
    <textarea name="note"></textarea>
  </label>
  ...
</form>
```

## Data plots (spectrogram/oscillogram)

Not a general convention — noted here only as a pointer, since it's easy to mistake for one.
Spectrogram and oscillogram images are stretched independently on both axes
(`object-fit: fill`) and the spectrogram grows via `flex: 1 1 auto` inside the drawer's
drag-resize. Full detail lives in this repo's root `CLAUDE.md` under "Derived media rendering" —
check there before touching either image's CSS.

## Standing rule: promote on second use

When a rule is written as page- or ID-scoped CSS for a single element, and a second page later
needs the same look, promote it into a shared class **in the same change** that adds the second
user — don't leave a second, near-duplicate copy sitting next to the first. This is the rule
`.filter-bar` above was created under: `map.html`'s `#filters` had this look first;
`sessions_list.html` needing the identical look later is what turned it into a class instead of
a second copy-pasted ID block. Apply the same judgment to the next repeat, whatever it turns out
to be.
