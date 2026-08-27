# Fledermap Style Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document Fledermap's existing UI conventions in a style guide, add a thin pointer
skill, and bring `sessions_list.html`/`session_detail.html` in line with them — including
extracting the one genuinely-duplicated pattern (the filter-bar look) into a shared CSS class.

**Architecture:** Pure templates/CSS/docs change, no Python or JS behavior change. Four
self-contained tasks: (1) the reference doc, (2) the `.filter-bar` shared class applied to both
`map.html` and `sessions_list.html`, (3) small additive CSS fixes for the remaining bare
elements on `session_detail.html`, (4) the pointer skill file.

**Tech Stack:** Jinja2 templates, hand-written CSS (`app.css`), no build step, no CSS/JS test
framework. Verification for the CSS/template tasks is a one-shot headless-Chrome screenshot
(`google-chrome --headless=new --screenshot=... --window-size=1280,900 <url>`) rather than an
automated test — this project has no automated visual testing (established precedent; see the
spec's Testing/Verification section).

**Spec:** `docs/superpowers/specs/2026-08-27-fledermap-style-guide-design.md`

## Global Constraints

- No CSS token refactor — reuse the existing `--color-*` custom properties in `app.css`'s
  `:root` as-is; do not add, rename, or restructure them.
- No CSS framework, no build step — hand-written CSS only, matching the rest of the project.
- No Python or `app.js` behavior changes. The `#filters` and `#session-filters` **ids** must be
  kept on their `<form>` elements even after `.filter-bar` is added as a class — `app.js:62`
  (`document.querySelector('#filters [name="site"]')`) depends on the `#filters` id.
  `hatch fmt --check`, `hatch run types:check`, and `hatch test` must stay green after every
  task (run with `dangerouslyDisableSandbox: true` per this repo's environment notes — Docker
  and sandboxed `git`/`curl` both need it here).
- **Standing rule for this and all future UI work** (goes in both the doc and the skill,
  verbatim in intent): a rule written as page- or ID-scoped CSS for one element gets promoted to
  a shared class in the same change that gives it a second user — never left duplicated.
- Every genuinely **shared, reusable** CSS class (meant to be applied on more than one element
  or page) must be documented in `docs/style-guide.md`'s "Shared classes" section, in the same
  task that introduces it — Task 1 documents `.filter-bar` and `.stacked-form` up front since
  Task 2 relies on the name already being decided. A **single-element scoping hook** (a class
  that exists only so one specific element can be targeted without a too-broad tag selector,
  with no intent of reuse) does not need a guide entry — the existing `.merge-badge` is this
  project's precedent, and Task 3's `.detail-meta` follows the same pattern. If a second element
  later wants `.detail-meta`'s look, that is what the standing "promote on second use" rule
  below is for — evaluate then, not preemptively now.

---

### Task 1: Write `docs/style-guide.md`

**Files:**
- Create: `docs/style-guide.md`

**Interfaces:**
- Consumes: nothing (pure documentation, describes CSS/classes that already exist in
  `src/fledermap/web/static/app.css` as of this plan's base commit).
- Produces: the `.filter-bar` class name and its documented purpose, which Task 2 implements in
  CSS to match; the `.stacked-form` class name, already implemented (round 1 of this session's
  UI work), documented here for the first time.

- [ ] **Step 1: Write the doc**

Create `docs/style-guide.md` with this content:

```markdown
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

| Token | Value | Use |
|---|---|---|
| `--color-text` | `#1a1a1a` | Primary body text |
| `--color-muted` | `#666` | Secondary/metadata text — labels, captions, timestamps |
| `--color-border` | `#d8d8d8` | Borders on inputs, panels, dividers |
| `--color-bg` | `#ffffff` | Page and control background |
| `--color-bg-subtle` | `#f7f7f8` | Panel/toolbar background, one step off white |
| `--color-accent` | `#2b6cb0` | Links and interactive accents |

Never hardcode a hex color in a new rule — use the token. If a new color is genuinely needed,
add it to `:root` and document it here in the same change.

## Spacing rhythm

Not an enforced scale — match the range already in use:

- **Container padding:** `0.75rem`–`1rem` (e.g. `#sidebar { padding: 0.75rem 0.5rem; }`,
  `.filter-bar { padding: 0.6rem 1rem; }`)
- **Internal gaps** (flex `gap`, spacing between sibling controls): `0.4rem`–`0.75rem`
- **Tight label-to-control spacing:** `0.25rem`–`0.6rem` (e.g. `.stacked-form label { margin-bottom: 0.6rem; }`)

## Form controls

Global element selectors in `app.css` already give every `<input>`/`<select>`/`<button>` inside
`.filter-bar` this look; nothing extra is needed to opt in beyond using the class:

```css
border: 1px solid var(--color-border);
border-radius: 4px;
padding: 0.3rem 0.5rem;
background: var(--color-bg);
color: var(--color-text);
font: inherit;
font-size: 0.9rem;
```

Bare `button` elements anywhere in the app already get a matching bordered look plus `:hover`/
`:disabled` states (`app.css`, global `button` rule) — no class needed for buttons.

## Shared classes

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
```

- [ ] **Step 2: Verify the doc reads correctly**

No automated check applies to a markdown file with no build step. Read the file back and
confirm every code snippet's class/property names match what Task 2 and the existing
`app.css`/`session_detail.html` actually contain (cross-check `.stacked-form`'s properties
against `app.css`'s existing rules at the time of this plan — `margin-bottom: 0.6rem` on
`.stacked-form label` is correct as of this plan's base commit).

- [ ] **Step 3: Commit**

```bash
git add docs/style-guide.md
git commit -m "docs: add the Fledermap style guide"
```

---

### Task 2: Extract `.filter-bar` and apply it to `map.html` and `sessions_list.html`

**Files:**
- Modify: `src/fledermap/web/static/app.css` (replace the `#filters`-scoped rules at lines
  62–82 with a `.filter-bar` class)
- Modify: `src/fledermap/web/templates/map.html:14` (add `class="filter-bar"` to
  `<form id="filters">`)
- Modify: `src/fledermap/web/templates/sessions_list.html:21-30` (add `class="filter-bar"` to
  the `<form id="session-filters" ...>` opening tag)

**Interfaces:**
- Consumes: the `.filter-bar` class name and CSS decided in Task 1's doc.
- Produces: nothing further tasks depend on — Task 3 touches different, non-overlapping
  selectors (`session_detail.html`'s back-link/detector-line, not the filter bar).

- [ ] **Step 1: Replace the `#filters`-scoped CSS with `.filter-bar`**

In `src/fledermap/web/static/app.css`, find this block (currently lines 62–82):

```css
#filters {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  flex-wrap: wrap;
  background: var(--color-bg-subtle);
  border-bottom: 1px solid var(--color-border);
}
#filters label { font-size: 0.85rem; color: var(--color-muted); }
#filters select,
#filters input {
  font: inherit;
  font-size: 0.9rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg);
  color: var(--color-text);
}
```

Replace it with:

```css
/* Shared by any horizontal toolbar of filter controls (map.html's #filters,
   sessions_list.html's #session-filters) -- see docs/style-guide.md. Both
   forms sit inside a `.main-content` flex column (app.css:56, a class rule
   that reaches both map.html's <div class="main-content"> and
   sessions_list.html's <main class="main-content">), but flex items default
   to flex-grow: 0 -- neither form stretches without an explicit flex: ... 1
   rule, so no shared or per-page override is needed here. #filters keeps its
   own flex: 0 0 auto only to also opt out of flex-shrink (map.html's #map
   sibling is flex: 1 1 auto and could otherwise shrink #filters below its
   content size in a cramped viewport); sessions_list.html has no such
   flex-growing sibling for #session-filters to be squeezed by, so it doesn't
   need the same override. */
#filters { flex: 0 0 auto; }
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

(This keeps the `#filters { flex: 0 0 auto; }` sizing rule that only makes sense for `map.html`'s
flex-column layout, while moving the visual toolbar look — the part `sessions_list.html` also
needs — into `.filter-bar`.)

- [ ] **Step 2: Apply the class in `map.html`**

In `src/fledermap/web/templates/map.html`, change line 14 from:

```html
    <form id="filters">
```

to:

```html
    <form id="filters" class="filter-bar">
```

- [ ] **Step 3: Apply the class in `sessions_list.html`**

In `src/fledermap/web/templates/sessions_list.html`, the form currently opens (lines 21–30):

```html
    <form
      id="session-filters"
      method="get"
      action="/sessions"
      hx-get="/sessions"
      hx-trigger="change"
      hx-target="#sessions-table-wrapper"
      hx-select="#sessions-table-wrapper"
      hx-push-url="true"
    >
```

Add `class="filter-bar"` as another attribute on the same element:

```html
    <form
      id="session-filters"
      class="filter-bar"
      method="get"
      action="/sessions"
      hx-get="/sessions"
      hx-trigger="change"
      hx-target="#sessions-table-wrapper"
      hx-select="#sessions-table-wrapper"
      hx-push-url="true"
    >
```

- [ ] **Step 4: Confirm `app.js`'s selector still works**

`app.js:62` selects `document.querySelector('#filters [name="site"]')`. This selects by id, not
class, and both are untouched by this task — confirm this stays true:

```bash
grep -n "#filters\|filter-bar" src/fledermap/web/static/app.js
```

Expected: only the existing `#filters [name="site"]` line (or nothing, if not present in this
plan's base commit) — no `.filter-bar` references, since nothing in `app.js` needs to change.

- [ ] **Step 5: Run the test suite and linters**

```bash
hatch fmt --check
hatch run types:check
hatch test
```

Expected: all green, no diffs (this task touches no Python).

- [ ] **Step 6: Visual verification**

Start the dev server and take one-shot headless-Chrome screenshots of both affected pages. Kill
anything already bound to the port first (a stale `fledermap serve` from an earlier run is a
known issue in this environment — see this repo's `CLAUDE.md`):

```bash
ss -ltnp 2>/dev/null | grep -E ':5001\s' || true
```

If that prints a PID still listening, `kill -9` it and re-check before continuing. Then:

```bash
nohup hatch run fledermap serve > "$TMPDIR/fledermap-serve.log" 2>&1 &
sleep 3
curl -sf http://127.0.0.1:5001/ > /dev/null && echo "server up"
```

Run with `dangerouslyDisableSandbox: true` (Docker/local-network access needs it here). Then:

```bash
google-chrome --headless=new --disable-gpu --screenshot="$TMPDIR/map-filterbar.png" \
  --window-size=1280,900 http://127.0.0.1:5001/
google-chrome --headless=new --disable-gpu --screenshot="$TMPDIR/sessions-filterbar.png" \
  --window-size=1280,900 http://127.0.0.1:5001/sessions
```

Read both PNGs back (the Read tool displays images). Confirm:
- `map.html`'s filter bar looks the same as before this task (regression check — this is a pure
  CSS extraction, the rendered result should be pixel-identical to what it was before).
- `sessions_list.html`'s filter bar now has the bordered background/inputs, matching the map
  page's filter bar, instead of bare unstyled controls.

If either looks wrong, fix the CSS before proceeding — do not move to Step 7 on a visual defect.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/static/app.css src/fledermap/web/templates/map.html \
  src/fledermap/web/templates/sessions_list.html
git commit -m "feat: extract .filter-bar shared class, apply to map and sessions filters"
```

---

### Task 3: Additive fixes for `session_detail.html`'s remaining bare elements

**Files:**
- Modify: `src/fledermap/web/static/app.css` (add two small rules)
- Modify: `src/fledermap/web/templates/session_detail.html:16` (add `class="detail-meta"` to
  the detector-line `<p>`)

**Interfaces:**
- Consumes: the `--color-accent` and `--color-muted` tokens (already defined in `app.css`'s
  `:root`, unchanged by this plan).
- Produces: nothing further tasks depend on.

- [ ] **Step 1: Confirm the exact elements to fix, and what else shares their tags**

```bash
grep -n "Back to sessions\|Detector:\|<p>" src/fledermap/web/templates/session_detail.html
```

Expected output (from this plan's base commit) includes both target lines plus a third `<p>`
that must NOT be touched by this task — the merge-banner's warning message:
```
12:    <a href="/sessions">← Back to sessions</a>
16:    <p>Detector: {{ detail.session.detector_key | detector_label }}</p>
44:      <p>
```
Line 44 is `.merge-banner`'s `<p>⚠ This session may merge with session ...</p>` — an alert that
should stay full-weight, not fade to muted secondary-text color alongside the detector line. A
blanket `main.main-content p { color: var(--color-muted); }` rule would catch it too (both are
inside `<main class="main-content">`), which is why this task gives the detector line its own
class instead of a bare tag selector.

- [ ] **Step 2: Add a class to the detector line**

In `src/fledermap/web/templates/session_detail.html`, change line 16 from:

```html
    <p>Detector: {{ detail.session.detector_key | detector_label }}</p>
```

to:

```html
    <p class="detail-meta">Detector: {{ detail.session.detector_key | detector_label }}</p>
```

- [ ] **Step 3: Add the CSS rules**

In `src/fledermap/web/static/app.css`, after the existing `main.main-content h1 { margin-top: 1rem; }`
rule (around line 58), add:

```css
/* session_detail.html's back-link and detector line render with browser
   defaults today -- no project color at all. The back-link rule is scoped to
   main.main-content rather than a bare `a` selector so it doesn't reach into
   map.html's or sessions_list.html's unrelated links; the detector line gets
   its own .detail-meta class rather than a bare `p` selector because
   main.main-content also contains the merge-banner's alert <p>, which must
   stay full-weight, not fade to muted text alongside it. */
main.main-content > a:first-child { color: var(--color-accent); }
.detail-meta { color: var(--color-muted); }
```

- [ ] **Step 4: Run the test suite and linters**

```bash
hatch fmt --check
hatch run types:check
hatch test
```

Expected: all green.

- [ ] **Step 5: Visual verification**

Reuse the running server from Task 2 (restart it with the same stale-port check if it's no
longer up):

```bash
curl -sf http://127.0.0.1:5001/ > /dev/null && echo "server up" || echo "need restart"
```

Find a real session id to screenshot:

```bash
curl -s http://127.0.0.1:5001/sessions | grep -oP '/sessions/\d+' | sort -u | head -1
```

Then, with `dangerouslyDisableSandbox: true`:

```bash
google-chrome --headless=new --disable-gpu --screenshot="$TMPDIR/session-detail-fix.png" \
  --window-size=1280,900 http://127.0.0.1:5001/sessions/<id-from-above>
```

Read the PNG back. Confirm:
- "← Back to sessions" renders in the accent blue, not default link-blue/black.
- "Detector: ..." renders in muted gray, not full-black body text.
- The `.stacked-form` layout and the map/form footer alignment (both fixed in earlier rounds of
  this session's work) are undisturbed — nothing about this task's CSS should touch either.

If anything looks wrong, fix the CSS before proceeding.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/static/app.css src/fledermap/web/templates/session_detail.html
git commit -m "fix: color the session detail page's back-link and detector line"
```

---

### Task 4: Add the pointer skill

**Files:**
- Create: `.claude/skills/fledermap-style-guide/SKILL.md`

**Interfaces:**
- Consumes: `docs/style-guide.md`'s path (Task 1) — the skill only points at it, does not
  duplicate its content.
- Produces: nothing further tasks depend on. Last task in this plan.

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/fledermap-style-guide/SKILL.md`:

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

- [ ] **Step 2: Verify the file was actually created**

This repo's sandbox has masked several `.claude/*` paths as `/dev/null` character-device stand-ins
in some sessions (see this repo's root `CLAUDE.md`, "Sandbox `/dev/null` masks are not real
files"). Confirm the write actually landed as a real file, not silently absorbed by a mask:

```bash
ls -la .claude/skills/fledermap-style-guide/SKILL.md
```

Expected: an ordinary regular file (`-rw-...`), owned by the real user, with today's date and a
nonzero size — not `crw-------`/`nobody:nogroup`. If it looks like a mask, stop and report this
to the user rather than guessing further (per this repo's `CLAUDE.md`: agent-side filesystem
checks can't reliably settle a masked-path question; a working `Write` followed by `ls -la`
showing a real, owned, nonzero-size file is about as far as this plan can verify from inside the
sandbox — if this file's directory turns out to be masked, `Write` itself would already have
failed loudly, which is a stronger signal than `ls` alone. If `Write` succeeded, proceed; only
stop if `Write` itself errored.)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/fledermap-style-guide/SKILL.md
git commit -m "feat: add fledermap-style-guide pointer skill"
```

---

## Final Verification

After Task 4:

```bash
hatch fmt --check
hatch run types:check
hatch test
```

All green, and take one more screenshot pass over `/`, `/sessions`, and a real `/sessions/<id>`
to confirm the whole set of changes reads consistently together (not just per-task in
isolation). Then hand off to `superpowers:finishing-a-development-branch` as usual — this plan's
branch was created via `superpowers:using-git-worktrees`/SDD setup at execution time, per that
skill's process.
