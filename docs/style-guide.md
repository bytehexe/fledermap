# Fledermap Style Guide

This documents the UI conventions already established by `app.css` and `map.html` — the
project's oldest and most-worked page — so later pages (`sessions_list.html`,
`session_detail.html`, and whatever comes after) match instead of drifting into inconsistent,
unstyled markup. It is a reference for matching what's already there, not a new design system:
no new spacing/type scale, no CSS framework, no build step. See
`docs/superpowers/specs/2026-08-27-fledermap-style-guide-design.md` for the reasoning behind
that scope.

**This guide must be updated in the same change that makes the decision it documents** (Janna,
2026-09-11) — a new shared class, a promoted rule, a placement call for a recurring element, a
scope note like the "Sample data" or "Migrations" sections elsewhere in this repo's other docs.
The "Standing rule: sweep on new rule" and "decide element placement once, project-wide" sections
below already assume this (they describe *writing the rule down*, not just following it once);
this note makes it explicit as its own standing rule so a decision doesn't ship only as a code
comment or a commit message and quietly go undocumented here. If a decision is close enough to an
existing section to extend rather than duplicate, extend that section (as the placement decision
under `.entity-header`/`.panel-header` below does) rather than adding a new one that could drift
from it.

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
| `--color-success` | `#2f855a` | `#48bb78` | `.save-confirmation`'s "✓ Saved" text — see "Positive save feedback" below |
| `--color-overlay` | `#ff2fd6` | `#ff2fd6` | Playback cursor and Ruler-tool measurement box — deliberately theme-invariant for visibility against both light and dark spectrograms |

Never hardcode a hex color in a new rule — use the token. If a new color is genuinely needed,
add it to `:root` and document it here in the same change.

## Dark mode

System preference by default (`prefers-color-scheme: dark`), with a three-state manual override
(system → light → dark → system) via the sidebar's theme-toggle button (`_nav.html`, three
sibling `device-desktop`/`sun`/`moon` icons swapped via `x-show` -- see "Icons" below), persisted
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

### No emoji or Unicode dingbats in the UI

Every icon goes through the `icon()` Jinja global (`web/icons.py`) — inline Tabler Icons SVGs,
never a raw emoji or dingbat character typed into a template or JS string. This replaced the
app's previous mix of Unicode glyphs (design spec
`docs/superpowers/specs/2026-09-13-fledermap-icon-set-design.md`), which repeatedly drifted
(mismatched glyphs across files, no real hollow/solid pair for an arbitrary concept, inconsistent
cross-platform rendering). A JS-driven icon swap (a toggle between two states) renders both as
sibling `<svg>`s and toggles the `hidden` attribute — never rewrites `.textContent`/`.innerHTML`
with a new glyph. A JS-created element that needs an icon (built via `document.createElement`,
with no way to call `icon()` itself) clones it from a hidden `<template>` the enclosing Jinja
template renders once, rather than duplicating the SVG string in JS where it could drift from the
vendored file (`_classifier_box.html`'s `#classifier-chip-remove-icon` template, cloned by
`classifier_box.js`, is the current example).

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
directly (no redundant nested card). That lone widget's chart still needs a `.stats-solo-chart`
wrapper around its `<canvas>` (a fixed `max-width`, no border) — without a sized parent, Chart.js's
responsive resize measures the full-width band and renders the chart far too large. `.stats-tile`
is a single stat number (a count), kept
distinct from `.entity-list`'s table rows since it's one number, not a row of comparable fields.
Never nest a bordered `.stats-panel` inside another `.stats-panel` — a card only ever nests
inside a tinted, borderless band.

### `.entity-header`

Use for the top-of-page block on any full standalone entity page (`site_detail.html`,
`species_detail.html`, `recording_details.html`) and, at drawer scale, `.panel-header` on
`_site_panel.html`/`_recording_panel.html`: a title on the left, that page's own action links
(Show on map, Statistics, Full page, …) right-aligned on the same row, wrapping to a second line
on narrow screens rather than truncating either side. Replaces three pages each hand-rolling
their own header shape (a `.panel-header` div, two loose `<p>` tags before the `<h1>`, a
favourite button floated next to `<h1>` with the rest of the actions buried in a meta paragraph)
— reconciled 2026-09-09 once the interlinking pass needed the same block on all three.

```html
<div class="entity-header">
  <h1>{{ title }}</h1>
  <div class="entity-header-actions">
    <a href="...">Show on map</a>
    <a href="...">Statistics</a>
  </div>
</div>
<p class="entity-header-subtitle">{{ subtitle line: counts, admin path, common names, ... }}</p>
```

**Placement decision (Janna, 2026-09-11): a recording's flag-for-review and favourite toggles
sit inside the same right-aligned actions row as the page's own action links, in that row's own
order — never before the title.** Concretely: `...`/`Full page` (drawer only), then the flag
toggle, then the favourite toggle last, so favourite is always the row's right-most element.
`recording_details.html` passes both buttons as `header_extra` (rendered after `actions` in
`.entity-header-actions`); `_recording_panel.html`'s drawer-scale `.panel-header` groups its
"Full page" link and both toggles into a single `.panel-header-actions` child instead of leaving
them as separate flex items of `.panel-header` itself — `.panel-header`'s own
`justify-content: space-between` only ever has two children (the `<h2>` and this one actions
group) to space apart; with the toggles as separate top-level children it spaced all four
individually across the row, which read as an unintentional centering effect rather than one
clustered group of actions. Previously the two toggles lived to the *left* of the title
(`recording_details.html`) or scattered as loose siblings after "Full page"
(`_recording_panel.html`) — this is the standing "decide element placement once, project-wide"
rule (below) resolving that drift; if either toggle appears on a future entity page, keep this
same order and grouping rather than re-deciding per page.

**Interlinking, two general principles** (Janna 2026-09-09) — the rest of this subsection is a
specific instance of these:

1. **Related entities are expected to link to each other, usually in both directions.** A site
   page lists its species and sessions; a species page lists its sites — if one direction exists,
   ask whether the other should too, rather than treating the first as the whole feature. Apply a
   bit more care for a *list* of related entities than a single one: a site linking every
   recording it has is fine, but a species page linking every single recording of it might not
   be once that list is large — that's exactly the "recent recordings, capped" shape
   `RECENT_RECORDINGS_LIMIT` already uses, not a reason to skip the link.
2. **Wherever an entity's name is written, it should be a link, unless there's a good specific
   reason it isn't.** Plain, unlinked text naming a site/species/session/recording is a gap to
   ask about, not a neutral default.

**An entity's own name/label, wherever it's rendered as a link, always points at that entity's
own dedicated page** — never a filtered view of something else, and never an in-place panel swap.
Before this rule, "click the site name" meant three different things depending on where you
stood: the map filtered to that site (`recording_details.html`'s meta line), the site's drawer
panel swapped in place (`_recording_panel.html`'s Site line, via `hx-get`), or (the one page that
had it right) the site's own `/sites/<id>` page (`_recording_panel.html`'s separate `(full
page)` link, `_site_panel.html`'s `Full site page`). Standardized on the last one everywhere: a
name link is always `/sites/<id>` (or `/species/<id>`, `/sessions/<id>`) — the destination one
click away, `/sites/<id>`, already carries its own "Show on map"/"Statistics" actions via this
same `.entity-header`, so nothing the map-filter or in-drawer shortcuts offered is actually lost,
just one hop further away. A page/panel that specifically wants the map-filtered view keeps that
as its own explicitly-labeled action (e.g. `_site_panel.html`'s "Show only this site" button) —
distinct from, and never substituting for, the entity's own name link.

Every drawer panel header (`.panel-header`) and every full-page `.entity-header`'s self-link
("this entity's own full page", when the page itself isn't already that full page) is labeled
**"Full page"**, not a per-entity phrase like "Details" or "Full site page" — same role, same
word, everywhere it appears.

## Timestamp display

Every displayed timestamp goes through one of three Jinja filters — never a bare
`.strftime()`/`.isoformat()` call on a stored timestamp:

- **`local_datetime`** — `YYYY-MM-DD HH:MM ZZZ` (e.g. `2026-09-06 20:29 CEST`). The default;
  used everywhere a zone-aware minute-resolution timestamp is shown.
- **`local_date`** — `YYYY-MM-DD`, no zone suffix. For date-only columns where a bare calendar
  date reads fine without one and there's no room for it (`sites_list.html`'s `last_at`).
- **`local_datetime_seconds`** — `YYYY-MM-DD HH:MM:SS ZZZ`. Only for `recording_details.html`
  and `_recording_panel.html`'s `recorded_at` line, where a user distinguishes one recording from
  a near-identical neighbor recorded seconds apart — everywhere else, minute resolution is enough.

A start–end range renders each end independently through the same filter (never a shared,
zone-shown-once macro), separated by a spaced en dash: ` – ` — e.g. `{{ s.started_at |
local_datetime }} – {{ s.ended_at | local_datetime }}`. Rendering each end through the filter
separately, rather than formatting the pair once, keeps the range consistent with every
single-timestamp use of the same filter without a second formatting path to keep in sync.

### Back-links (`return_to`)

A detail page reachable from more than one place (the map drawer, a table row on another
entity's page, another detail page's meta line) accepts an optional `return_to` query param — a
same-origin relative path, validated with `web/params.py`'s `is_safe_relative_path` before it's
trusted for anything (never build a second copy of that check; `recording_detail.py`'s original
private copy was promoted there for exactly this reuse) — and turns it into that page's back
link, falling back to a sensible page-specific default when absent, invalid, or from an
unrecognised origin (see `recording_details.html`'s `_resolve_back_link` for the shape: known
origins get a specific label like "Back to map"/"Back to sessions", anything else safe just gets
"Back", anything unsafe or missing falls back to the page's default). A page reachable from
essentially one place doesn't need this — don't add `return_to` support speculatively.

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

## Scrolling

**A page should not produce an unintended scrollbar, horizontal or vertical.** The
recording-detail page's vertical scrollbar was treated as a real defect and eliminated via
`fitDetailHeight()`'s shrink-to-fit (see this repo's root `CLAUDE.md`, "Derived media rendering")
— that fix was specific to that page's fixed-aspect content, but the underlying judgment
generalizes: a scrollbar appearing where nothing about the page's content requires one (an
overflowing fixed-width element, an unaccounted-for margin, a flex child that doesn't shrink) is a
layout bug to find and fix, not a fact of the page. The site-detail page's horizontal scrollbar is
the currently-open instance of the *horizontal* case — no shrink-to-fit exists for it yet, only
the vertical precedent above.

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
- **A successful save gives positive feedback, not just silence.** Show a `.save-confirmation`
  span ("✓ Saved", `color: var(--color-success)`) next to the Save button after a save succeeds —
  without it, nothing tells the user their click actually did anything, which reads as broken
  even when it worked. Two shapes exist, pick whichever matches how the form saves:
  - **An AJAX save that swaps its own markup back in** (`_classifier_box.html`'s
    `classifier_box.js`): the confirmation renders `hidden` in the server-side template and the
    save handler un-hides it, then hides it again after a couple of seconds (`setTimeout`) —
    needed because the swap re-creates the span fresh each time, so its own initial `hidden` state
    can't be "already shown" from a previous save.
  - **A plain `<form method="post">` that redirects back to itself** (`session_detail.html`'s
    edit form): the handler redirects with a `?saved=1` query param, and the template renders the
    confirmation only `{% if request.args.get('saved') %}` — there's no swap to piggyback on, so
    the redirect itself carries the "just saved" signal.
- **A page holding unsaved changes must warn before it's left unsaved** (navigating away, closing
  the tab). Track a dirty flag (current form values differ from what was last loaded/saved) and
  register a `window.addEventListener("beforeunload", ...)` listener that calls
  `event.preventDefault()` (and sets `event.returnValue = ""`, for the browsers that still need
  it) whenever that flag is true; clear the flag on a successful save. This is the only mechanism
  to use — not a custom in-page modal, which cannot intercept a tab close or URL-bar navigation
  the way `beforeunload` does. It only applies to actual page navigation; an htmx partial swap
  that never leaves the page has nothing to guard.

  A plain `<form method="post">`'s own submit is itself a navigation — without clearing the dirty
  flag in a `submit` listener first, clicking the form's own Save button triggers the exact same
  "leave without saving?" dialog this exists to prevent for everything else, since the save
  hasn't round-tripped to the server yet by the time `beforeunload` fires. `unsaved_changes_guard.js`
  (`session_detail.html`'s two forms — the session edit form and the merge-resolution form, per
  the shared-class "promote on second use" rule) is the reusable version of this; an AJAX save
  flow instead clears its own flag directly in the save handler once the request actually
  succeeds (`classifier_box.js`'s `classifierBoxDirty`), which has no equivalent premature-fire
  problem since an AJAX request is never itself a navigation.
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

## Standing rule: sweep on new rule

Almost every open `[!!flag:UI Consistency]` backlog item traces back to the same shape: a rule
above got written *after* someone noticed one violation, and only that one spot got fixed —
nothing then swept the rest of the templates for the same shape. The disable-don't-hide rule (in
"Interaction & data-safety rules" above) shipped with two pre-existing violations left open rather
than fixed alongside it; the interlinking rules stayed explicitly non-exhaustive by their own
text. Same discipline as CLAUDE.md's "grep for every reader" rule for code removals, applied here:
**when a rule is added because an existing page violated it, grep the other templates for the
same shape in that same change**, and file any deferred fixes as explicit backlog items rather
than leaving them implicit in the rule's own prose.

## Standing rule: decide element placement once, project-wide

When a UI element that plausibly recurs across entities gets added (a favourite button, a
"jump to full page" link, a status badge), its position within the shared header/panel shape is a
project-wide decision to make explicitly in that same change, not a per-page judgment call to
re-improvise every time it comes up. Two concrete cases where this wasn't done:

- The favourite button's position drifted between the details page and the drawer panel — each
  was built without asking "where does this element go, everywhere it appears," so each landed it
  somewhere slightly different. `.entity-header-actions` documents that the *action links* are
  right-aligned as a group; it never said the favourite button belongs in that same group, so nothing
  pinned its position down when a second page added it.
- The "Full page" self-link's *wording* was standardized (see `.entity-header` above), but not its
  *position* — the rule fixed what the link says without also fixing where in the header/panel it
  sits, so site and recording ended up saying the same word in different places.

The failure mode is specifically **silent relocation**: the element's natural/previous spot is
already occupied by something else on the new page, and rather than that conflict forcing a
decision (does the existing occupant move, does the new element take a different but *consistent*
spot everywhere, is the shared shape itself wrong), the new element just goes wherever fits on
that one page and the conflict is never resolved for the project as a whole. When adding a
recurring element, or reusing one on an entity that already has something in its usual slot: name
the target position as a rule (extend `.entity-header`/`.panel-header`'s own documentation, the
way the "Full page" wording rule did, but for position too), apply it everywhere the element
already exists in the same change, and if the slot's occupied, decide the conflict outright rather
than parking the new element in whatever space happens to be free.

**Timing: before the spec, not during implementation.** When a spec will add, redesign, or
re-place a UI element, this decision — sweep the live app for every other place the
element/pattern appears, decide its position against this guide, mock up if the guide doesn't
already settle it — happens *before the spec is drafted*, so the spec states the decision instead
of leaving it open ("follows the existing X pattern" when X's own placement is inconsistent,
"disabled/absent" instead of picking one). A UI change with no spec (a quick fix) does the same
step immediately before making the change. See CLAUDE.md's "UI consistency process" for the
process pointer and the outstanding spec this applies to.

**A mockup is required, not optional, in two checkable cases** — neither is a judgment call about
"is this ambiguous":

1. The element is joining a region that already holds two or more other elements — a header, an
   action row, a panel with existing buttons/links. Count what's already in the target region.
2. The spec introduces a new page or a large UI surface with multiple sub-views/sections (several
   panels, a layout with distinct regions, a page combining more than a couple of independently
   meaningful blocks) — regardless of whether anything else already occupies that space, since
   there's nothing to count yet. The mockup can be as rough as the surface is large: a full new
   page's mockup is a coarse box-level sketch of its regions, not a fully-detailed rendering of
   every element within them — scale the effort to what's actually being decided (relative
   position and size of the sub-views), not to the page's total content.

Below both thresholds (a lone save button, a form field going into an existing `.stacked-form`, a
single self-contained addition with nothing else nearby to arrange against), an existing component
rule already settles it and a mockup adds nothing — requiring one there is ceremony, not
decision-forcing. A mockup doesn't substitute for the sweep-across-the-live-app step above,
either: it clarifies where things sit *within this one spec's layout*, not whether that layout
agrees with what another page already did — the sweep is what catches that.

**The mockup goes in the spec itself, and stays cheap.** Not a full render of the page and not a
real screenshot — a hand-drawn schematic (photographed/scanned) or an ASCII box diagram in a
fenced code block, whichever is fastest to produce for the case at hand. Its only job is showing
where the element sits relative to what's already there, checkable by someone reading the spec —
not a design deliverable in its own right. Prefer plain ASCII in a code fence by default over a
PlantUML Salt wireframe: a spec is read as raw text, by an agent and by a human in an editor (VS
Code), neither of which renders PlantUML — Salt only becomes a viewable diagram after an active
render step into an image, which isn't "obtained easily" for either reader. ASCII needs no such
step; it's already legible exactly where the spec lives.

**The decision must survive into the plan's own task text, not just the spec.**
`superpowers:subagent-driven-development` dispatches each task's implementer with a *task brief* —
that task's text extracted verbatim from the plan — and explicitly never gives it the whole plan
file, let alone the spec; whatever the dispatching controller doesn't separately add to the
dispatch is invisible to it. If a task's own text says "add the flag toggle button" without
restating *where* (the mockup, or the decided position in words), the implementer has nothing to
go on and will place it wherever seems reasonable for that task in isolation — which is exactly
how the fav-button drift happened: two pieces of work, each locally reasonable, disagreeing with
each other because neither one carried the other's decision forward. When writing a plan from a
spec that made a placement decision, restate that decision (or reproduce the mockup) in the
specific task(s) that implement it — in the task's own text, not just the plan's spec-link header.
