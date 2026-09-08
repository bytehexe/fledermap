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
| `--color-overlay` | `#ff2fd6` | `#ff2fd6` | Playback cursor and Ruler-tool measurement box — deliberately theme-invariant for visibility against both light and dark spectrograms |

Never hardcode a hex color in a new rule — use the token. If a new color is genuinely needed,
add it to `:root` and document it here in the same change.

## Dark mode

System preference by default (`prefers-color-scheme: dark`), with a three-state manual override
(system → light → dark → system) via the 🖥️/☀️/🌙 button in the sidebar (`_nav.html`), persisted
in `localStorage` under the key `fledermap-theme`. The override is applied via a `data-theme`
attribute on `<html>`, set by a small inline script (`_theme_init.html`, included immediately
after `<meta charset>`, before everything else in every page's `<head>`) *before* first paint, so
a returning visitor with an active override never sees a flash of the wrong theme.

Any rule that uses the tokens above gets dark mode for free — nothing extra to do. A rule that
hardcodes a color instead does not, and violates the "never hardcode a hex" rule above for
exactly this reason: `--color-warning` was promoted from `.merge-badge`'s hardcoded hex
specifically so it could have a dark counterpart.

Native control chrome (date-picker calendar icon/popup, `<audio controls>`, scrollbars, `<select>`
popups, checkboxes) isn't themed by these tokens at all — it's themed by the CSS `color-scheme`
property, set alongside the tokens (`color-scheme: light` in `:root`, `color-scheme: dark` in both
dark-mode blocks) rather than by the tokens themselves.

**The map does not theme.** Both Leaflet map instances (the main map, the session-detail
mini-map) keep their normal light appearance in every mode, deliberately — see
`docs/superpowers/specs/2026-08-28-fledermap-dark-mode-design.md`'s Non-goals. Don't "fix" the
map's light tiles as a perceived oversight.

**The oscillogram waveform also does not theme.** `media/oscillogram.py` renders a hard-coded
black-on-white waveform PNG, cached on disk by `params_hash` — out of scope for the same reason
as the map. Unlike the map's CSS, it's a rendered, cached image, so it cannot follow `data-theme`
the way the rest of the UI does even if it were in scope.

The `@media (prefers-color-scheme: dark)` block and the `:root[data-theme="dark"]` block in
`app.css` duplicate the same declarations — a media-query rule and a plain attribute-selector
rule can't be merged — so changing a dark-mode token value means editing it in *both* blocks, or
a future edit will drift between "system-driven dark" and "explicit dark override".

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

### `.entity-list`

Use for any table listing rows of the same kind of thing (species, sites, sessions, recordings)
where each row shares two or more comparable fields (a name/link plus a count, a date, a
category). Gives the table full width, collapsed borders, and bordered/padded cells — shares its
row styling with `#sessions-table` (`app.css`). See "Lists vs. tables" below for when a table is
the right call over a plain `<ul>`.

```html
<table class="entity-list">
  <thead><tr><th>Name</th><th>Count</th></tr></thead>
  <tbody>
    <tr><td><a href="...">...</a></td><td>3</td></tr>
  </tbody>
</table>
```

### `.stats-band` / `.stats-panel` / `.stats-tile`

Statistics dashboard layout (see the statistics design spec's "Page layout" section for the full
rationale). `.stats-band` (tinted background, no border) groups a page's content into a handful
of macro-sections, each labeled with `.band-label` (small bold/uppercase caption) rather than a
full `<h2>`. `.stats-panel` (bordered, own `.stats-panel-header`) wraps an individual chart/list
ONLY where a band holds 2+ widgets meant to be compared side by side — laid out via
`.stats-panel-grid`'s responsive `auto-fit` grid, which adapts to however many cards a band
actually has rather than assuming a fixed count. A band holding exactly one widget renders it
directly (no redundant nested card). `.stats-tile` is a single stat number (a count), kept
distinct from `.entity-list`'s table rows since it's one number, not a row of comparable fields.
Never nest a bordered `.stats-panel` inside another `.stats-panel` — a card only ever nests
inside a tinted, borderless band.

## Lists vs. tables

Prefer a table (`.entity-list`, see above) over a bulleted `<ul>` once rows share two or more
comparable fields — a name plus a count, a date, a category. A table gives those fields aligned
columns a reader can scan down, which a `<ul>` of `"Name: count"` strings doesn't. Species/site
detail pages' species-breakdown and sessions lists, and the session detail page's recordings
list, were originally plain lists and read noticeably worse than the equivalent table once both
existed side by side (2026-09-08) — converted, not left as a judgment call per page. A plain
`<ul>` stays the right choice for a single-fact enumeration with nothing to align into columns
(e.g. a caption's "what's excluded" note, or a form's list of validation errors).

This is also why the two full standalone entity-detail pages (`species_detail.html`,
`site_detail.html`) don't use `.panel-columns`, even though their content started as a copy of
the drawer panels that do: `.panel-columns`' side-by-side boxes exist to fit a few short lists
into the drawer's narrow, height-constrained space. A full page has the opposite constraint —
plenty of width, and a table wants room to breathe — so these two pages stack their tables
full-width instead. The drawer panels (`_site_panel.html`, `_recording_panel.html`) keep
`.panel-columns` unchanged; the tradeoff that motivates it there still holds.

## Data plots (spectrogram/oscillogram)

Not a general convention — noted here only as a pointer, since it's easy to mistake for one.
Spectrogram and oscillogram images are stretched independently on both axes
(`object-fit: fill`) and the spectrogram grows via `flex: 1 1 auto` inside the drawer's
drag-resize. Full detail lives in this repo's root `CLAUDE.md` under "Derived media rendering" —
check there before touching either image's CSS.

## Interaction & data-safety rules

These are behavioral conventions, not CSS — they apply regardless of which shared class a page
uses. Some existing pages don't fully comply yet (tracked in the backlog); write new UI to this
standard even where an older page hasn't been brought up to it yet.

- **A filter applies immediately, with no save step.** This is what `.filter-bar` above already
  means by "applies a query — typically live, on `change`": nothing a filter does is destructive
  or hard to redo, so there's nothing a save/confirm step would protect. If a control changes
  what's displayed rather than what's stored, it belongs in this category even outside
  `.filter-bar` itself.
- **A form that changes stored data needs an explicit Save**, and nothing it does should take
  effect before Save is pressed. This is the opposite default from filters above, and **a single
  form must never mix the two** — a "pick what to show" control and a "what to store" control
  never belong in the same `<form>`, even if they'd otherwise sit next to each other visually.
  Split them into two separate forms (they can still be laid out side by side) rather than
  building one form that partly live-applies and partly waits for Save.
- **The Save button is the form's default/primary action** — use the `.button-primary` class
  (`app.css`) on it, consistently, on every form, everywhere: accent background/border + bold
  text, the same "this one matters" visual language `.tool-button[aria-pressed="true"]` and
  `#view-lock-toggle[aria-pressed="true"]` already use for their active state, rather than a
  second one. A form's other buttons (Cancel, a destructive action gated per the rule below) stay
  plain `button` — only the one default/primary action per form gets `.button-primary`.

  ```html
  <button type="submit" class="button-primary">Save</button>
  ```
- **A page holding unsaved changes must warn before it's left unsaved** (navigating away, closing
  the tab). Track a dirty flag (current form values differ from what was last loaded/saved) and
  register a `window.addEventListener("beforeunload", ...)` listener that calls
  `event.preventDefault()` (and sets `event.returnValue = ""`, for the browsers that still need
  it) whenever that flag is true; clear the flag on a successful save. This is the only mechanism
  to use — not a custom in-page modal, which cannot intercept a tab close or URL-bar navigation
  the way `beforeunload` does. It only applies to actual page navigation; an htmx partial swap
  that never leaves the page has nothing to guard.
- **No single click may overwrite or delete stored data**, other than pressing Save itself (which
  the two rules above already gate behind a deliberate action and a visually distinct button).
  A destructive control (clear, delete, reset-to-default) must not take effect immediately:
  either stage the change behind the form's own Save (so leaving without saving discards it, per
  the unsaved-changes warning above), or — for an action with no form/Save step to stage behind,
  such as deleting a whole record — gate it behind `window.confirm("...")` and only act if it
  returns `true`. Use `confirm()` specifically, not a custom modal: it needs no markup, no CSS, no
  focus-trap logic, and reads unambiguously as a yes/no gate to every user regardless of page —
  the same reasoning as `beforeunload` above.
- **A dedicated Cancel/discard button never needs a confirmation of its own** — the unsaved-changes
  guard above exists for *accidental* navigation; clicking Cancel already states the intent to
  discard, so gating it behind another confirm would just be a second click standing in for the
  first. Wire Cancel to clear the dirty flag before it navigates or resets the form, so the
  `beforeunload` listener doesn't also fire for a deliberate discard.
- **UI elements are disabled, never hidden.** Removing a control from the layout when it's
  unavailable breaks muscle memory and reflows whatever's around it; disabling it in place keeps
  its position stable. This was already the deciding call for the recording-detail page's locked
  scrollbar (`.detail-scroll.view-locked` in `app.css`) over hiding it — extend the same judgment
  to buttons, form fields, and any other control.
- **A disabled element must be visually unmistakable as disabled** — greyed out, not merely
  unresponsive. A real `<button>`, `<input>`, `<select>`, or `<textarea>` gets this for free from
  its native `disabled` attribute plus `app.css`'s `:disabled` rules (`opacity: 0.5` today —
  extend that rule if a control type needs a different treatment, in the same change). An `<a>`
  styled and used as a button, or any custom widget, has no native `disabled` attribute to set —
  use `aria-disabled="true"` plus a shared `.is-disabled` class (`opacity: 0.5`, `cursor: default`,
  `pointer-events: none` — matching `:disabled`'s own look) instead of inventing a one-off
  treatment per widget, and check `aria-disabled` at the top of its click handler too:
  `pointer-events: none` stops a mouse click but not a `Enter`/`Space` keydown reaching a
  focusable element. A native control with no CSS disabled state at all (a scrollbar) needs its
  own hand-rolled treatment — see the comment above `.detail-scroll.view-locked` for how that
  case was solved when the platform gave it nothing to opt into.
- **A disabled element must not show a hover effect.** A native `<button disabled>` still
  matches `:hover` in every major browser (unlike `:active`/click, which it correctly
  suppresses) — without a guard, a disabled button visibly reacts to the mouse as if it were
  clickable, contradicting the "visually unmistakable as disabled" rule above the moment a
  cursor passes over it. Scope every `:hover` rule that applies to buttons with
  `:not(:disabled)` (`app.css`'s `button:hover`/`.button-primary:hover`). The `.is-disabled`
  custom-widget class doesn't need the same guard: its `pointer-events: none` already suppresses
  `:hover` entirely, not just click.

## Standing rule: promote on second use

When a rule is written as page- or ID-scoped CSS for a single element, and a second page later
needs the same look, promote it into a shared class **in the same change** that adds the second
user — don't leave a second, near-duplicate copy sitting next to the first. This is the rule
`.filter-bar` above was created under: `map.html`'s `#filters` had this look first;
`sessions_list.html` needing the identical look later is what turned it into a class instead of
a second copy-pasted ID block. Apply the same judgment to the next repeat, whatever it turns out
to be.
