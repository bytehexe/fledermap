# Fledermap Style Guide — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing
an implementation plan.
**Date:** 2026-08-27

## Problem

Fledermap's UI grew ad-hoc across Phase 4/5a/5b. `map.html`/`app.css` already carry a
reasonably coherent look (color tokens, a bordered filter bar, consistent spacing) — this was
fixed for the main page earlier in this project. `sessions_list.html` and `session_detail.html`
were added later and don't fully match: most concretely, `#session-filters` (the sessions list's
filter form) has **no CSS at all** and renders as bare browser-default controls, next to
`#filters` (the main map's filter bar) which has real styling — border, padding, spacing,
bordered inputs. That gap is the "90s design" the user is pointing at. Smaller bare-element gaps
exist elsewhere on both pages (headings, the back link, the merge badge).

The request is two-fold: (1) stop this from recurring by writing down the conventions that
already exist so future UI work follows them, and (2) fix the two pages that currently don't
match.

## Goals

- A written style guide (`docs/style-guide.md`) documenting the conventions **already
  established** by `app.css`/`map.html` — not a new design invented from scratch.
- A thin project skill that points UI work at the guide.
- Bring `sessions_list.html` and `session_detail.html` into line with those conventions.
- Where a pattern is genuinely shared across pages (the filter-bar look), factor it into one
  reusable CSS class instead of leaving each page to redefine it — this is the one place a
  refactor is worth it, because it is the exact shape of gap that produced the current
  inconsistency and would otherwise recur on the next new page.
- Everything else that doesn't recur yet (this page's heading spacing, that page's link color)
  stays a small, page-scoped, additive CSS rule — no invented shared abstractions for one-off
  elements.

## Non-goals

- No CSS token refactor (no new spacing/type scale, no restructuring of the existing
  `--color-*` custom properties). `app.css`'s existing tokens are the baseline as-is.
- No CSS framework or build step. The project has neither and this doesn't introduce either.
- No automated visual/CSS testing. None exists in this project; verification stays manual (CDP
  screenshots), the same pattern already used for this session's earlier UI fixes.
- No change to `app.js` behavior, routes, or any Python code. This is templates + CSS + docs
  only.

## Design

### 1. `docs/style-guide.md`

A short reference doc, prose plus small CSS snippets, covering:

- **Color tokens** — `--color-text`, `--color-muted`, `--color-border`, `--color-bg`,
  `--color-bg-subtle`, `--color-accent` (defined in `app.css`'s `:root`) and when each is used
  (body text vs. secondary/muted text, borders, page background vs. panel background, links/
  interactive accents).
- **Spacing rhythm** — the paddings and gaps already in use (`0.75rem`–`1rem` for
  container padding, `0.4rem`–`0.75rem` for internal gaps, `0.25rem`–`0.6rem` for tight
  label-to-control spacing), stated as the range to match rather than a new enforced scale.
- **Form controls** — the bordered `input`/`select` look (`1px solid var(--color-border)`,
  `border-radius: 4px`, `padding: 0.3rem 0.5rem`, `background: var(--color-bg)`) and the
  existing `button`/`button:hover`/`button:disabled` rules — both already global element
  selectors in `app.css`, so any new page gets them for free and the guide just says so.
- **`.stacked-form`** (existing, from round 1) — when to use it: any form whose fields should
  stack top-to-bottom (label above control) rather than flow inline. Documents the class that
  already exists; no changes to it.
- **`.filter-bar`** (new, see §2) — when to use it: any horizontal toolbar of filter controls
  (dropdowns, date inputs, checkboxes) that applies a query live via `change`.
- A short "why" paragraph pointing at this design doc and the CLAUDE.md UI conventions
  (`object-fit: fill` for data plots, `flex: 1 1 auto` for grow-to-fill columns) so the two
  don't drift apart.
- **A standing rule for future changes**, not just a one-time decision made in this pass: when a
  rule written as page-scoped/ID-scoped CSS for a single element is later needed by a second
  page, that's the signal to promote it into a shared class at that point — the same judgment
  call this design made for `.filter-bar` (§2), applied going forward rather than only now. The
  guide states this explicitly so it's not re-litigated per page: "a one-off rule that gains a
  second user becomes a shared class in the same change that adds the second user, not left
  duplicated." This is the mechanism that keeps the gap that caused this whole request (map's
  filter bar styled, sessions' filter bar not) from recurring under a different pair of pages.

### 2. `.filter-bar` shared class

Today `#filters` (map.html) carries the toolbar look via ID-scoped rules
(`app.css:62-82`). `#session-filters` (sessions_list.html) has no equivalent and renders
unstyled. Both are the same pattern: a horizontal, wrapping row of labeled filter controls with
a subtle background and bottom border.

Extract that pattern into a `.filter-bar` class:

```css
.filter-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  flex-wrap: wrap;
  background: var(--color-bg-subtle);
  border-bottom: 1px solid var(--color-border);
}
.filter-bar label { font-size: 0.85rem; color: var(--color-muted); }
.filter-bar select,
.filter-bar input {
  font: inherit;
  font-size: 0.9rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg);
  color: var(--color-text);
}
```

- `map.html`'s `<form id="filters">` gains `class="filter-bar"`; the old `#filters { ... }`,
  `#filters label { ... }`, `#filters select, #filters input { ... }` rules in `app.css` are
  deleted (superseded by the class rules). The `#filters` **id** stays — `app.js:62`
  (`document.querySelector('#filters [name="site"]')`) selects by it, and it costs nothing to
  keep.
- `sessions_list.html`'s `<form id="session-filters">` gains `class="filter-bar"` — this alone
  fixes the reported gap (no id-scoped rule needs writing for this form at all).
- `main.main-content` on `map.html` currently wraps `#filters` flush against `#map` with no
  page padding (a full-bleed toolbar); `sessions_list.html`'s `main` has page padding
  (`main.main-content { padding: 0 1.5rem 1.5rem; }`, `app.css:57`) applied to everything inside
  it including the filter bar, which is correct for a page-level toolbar sitting above a table
  rather than a full-bleed map. No change needed here — `.filter-bar`'s own padding plus the
  page's padding is the intended look for `/sessions`, distinct from the map page's edge-to-edge
  bar.

### 3. Page-scoped additive fixes (session_detail.html + sessions_list.html)

Everything that isn't the filter-bar pattern gets small, targeted rules in `app.css`, following
existing conventions, no new shared classes:

- `sessions_list.html`: the `<a>Back to sessions` link styling is inherited from browser
  defaults with no project color — give it `color: var(--color-accent)` to match every other
  link in the app (`.sidebar-link`, `.merge-badge` already do this). `<h1>` gets the same
  top-margin treatment `main.main-content h1` already applies (verify it already covers this
  page — it's a bare `main.main-content h1` selector, so it should; confirm during
  implementation and only add a rule if it doesn't).
- `session_detail.html`: same back-link fix; the `<p>Detector: ...</p>` line should use
  `color: var(--color-muted)` to match how secondary metadata reads elsewhere (e.g.
  `.panel-columns h3`, `#filters label`).
- Any other bare element found during implementation that clearly falls under an existing
  documented convention (a link that should be `--color-accent`, secondary text that should be
  `--color-muted`) gets fixed the same way — small, additive, cited against the guide.
- Elements that are already styled (`.stacked-form`, `#sessions-table`, `.merge-banner`,
  `.session-map-col`/`#session-mini-map` from the round-2 fix) are not touched — they already
  match.

### 4. `.claude/skills/fledermap-style-guide/SKILL.md`

A thin pointer skill, following this project's existing skill-file conventions (frontmatter with
`name`/`description`):

```markdown
---
name: fledermap-style-guide
description: Use before writing or reviewing any Fledermap HTML/CSS (templates, app.css) — points at the project's UI conventions.
---

# Fledermap Style Guide

Before writing or modifying any template or CSS in `src/fledermap/web/`, read
`docs/style-guide.md` — it documents this project's established color tokens, spacing rhythm,
form-control styling, and shared classes (`.stacked-form`, `.filter-bar`). Match it; don't
invent new patterns for something the guide already covers.

If a change reuses a rule that today is written page- or ID-scoped for one element, promote it
to a shared class in the same change — don't leave a second copy sitting next to the first.
```

No enforcement mechanism (no lint rule, no CI check) — this is a documentation + convention
tool, consistent with "Document existing conventions only" being the chosen scope, not a
tokens-and-tooling initiative.

## Testing / Verification

- No Python behavior changes: `hatch fmt --check`, `hatch run types:check`, and `hatch test`
  must stay green exactly as before (confirms no accidental regression — this work is templates
  + CSS + docs only).
- `app.js:62`'s `#filters [name="site"]` selector and any test asserting on the `#filters`/
  `#session-filters` **ids** are unaffected — both ids are kept; `.filter-bar` is added as an
  additional class, not a replacement. Grep confirmed (2026-08-27) no test currently asserts on
  `#filters`'s CSS or class list.
- Manual CDP screenshot pass, before/after, covering:
  - `map.html` — regression check, since `#filters`'s CSS moves to `.filter-bar` (same visual
    result expected, verifying the extraction didn't drop anything).
  - `sessions_list.html` — confirms the filter bar now matches the map page's look, and the
    other page-scoped fixes render correctly.
  - `session_detail.html` — confirms the back-link/detector-line fixes render correctly and
    don't disturb the already-fixed `.stacked-form`/map-alignment layout from the two earlier
    rounds.
- No new automated visual/CSS tests — none exist in this project for any prior UI change either
  (established precedent this session).

## Open items for implementation

None outstanding in the design itself. This request was classified Architectural at the start
(new project infrastructure: a doc + a skill), and that classification doesn't downgrade just
because the resulting diff turned out modest (two new files, edits to `app.css`, `map.html`,
`sessions_list.html`, `session_detail.html`) — the next step after user review is a
`writing-plans` implementation plan, however few tasks it ends up having, not direct
implementation.
