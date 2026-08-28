# Fledermap Dark Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dark mode to Fledermap's web UI (map, sessions list, session detail, nav) —
system-preference by default, a persisted manual override, no flash of the wrong theme on load —
and document it in the style guide.

**Architecture:** Every color in the app already routes through six `--color-*` custom
properties in `app.css`'s `:root` (confirmed by grep — no other hex literals or named CSS colors
exist in the stylesheet, and no template has inline color styling). Dark mode is: a dark-mode
value for each token (plus one new token, `--color-warning`, promoted from the one hardcoded hex
that does exist), selected via `prefers-color-scheme` with a `[data-theme]` attribute override,
applied before first paint by a tiny inline script, toggled by a plain Alpine component in the
already-shared `_nav.html`. Two tasks: (1) the tokens, the CSS selector mechanism, and the
anti-flash script wired into all three pages — dark mode already works via system preference
after this task alone; (2) the manual toggle plus the style guide update.

**Tech Stack:** Jinja2 templates, hand-written CSS (`app.css`, existing custom-property tokens),
Alpine.js (already loaded on every page via `_nav.html`'s inclusion) for the toggle — no new JS
file, no build step, no CSS framework. Verification is headless-Chrome screenshots, the same
technique the style-guide plan used — this project has no automated visual/CSS test framework.

**Spec:** `docs/superpowers/specs/2026-08-28-fledermap-dark-mode-design.md`

## Global Constraints

- No CSS framework, no build step, no new spacing/type scale — hand-written CSS only, reusing
  the existing token mechanism.
- **The map does not theme.** Neither Leaflet map instance (the main map in `map.html`, the
  session-detail mini-map) changes appearance in dark mode — confirmed with the user during
  brainstorming, not an oversight. No task in this plan touches Leaflet/MarkerCluster CSS,
  `app.js`'s `TAXON_PALETTE`, or `app.js`'s verdict marker colors.
- Never hardcode a hex color in new or modified CSS — route through a token. `.merge-badge`'s
  existing hardcoded `#b7791f` is converted to `var(--color-warning)` in Task 1, not left as a
  pre-existing exception.
- `data-theme` on `<html>` is only ever the literal string `"light"` or `"dark"` when present —
  never `"system"`. The attribute's *absence* means "follow system preference"; that's what lets
  `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { ... } }` correctly let
  an explicit light override defeat a dark system preference.
- The anti-flash init script (`_theme_init.html`) stays a small **inline** `<script>` block
  (never a separate `.js` file fetched over the network, which would run too late to avoid a
  flash) and is included in each page's `<head>` **before** `app.css`'s `<link>`.
- No Python or `app.js`/`session_map.js` behavior changes. `hatch fmt --check`,
  `hatch run types:check`, and `hatch test` must stay green after every task — run with
  `dangerouslyDisableSandbox: true` (Docker-backed tests and `git` both need it in this repo, per
  `CLAUDE.md`).
- Every dark-mode-relevant convention (the token pairs, the mechanism, the map non-goal) must be
  documented in `docs/style-guide.md` in the task that completes the feature (Task 2) — per that
  guide's own standing rule that a genuinely shared convention gets documented in the same change
  that introduces it.

---

### Task 1: Color tokens, dark-mode CSS mechanism, anti-flash script

**Files:**
- Modify: `src/fledermap/web/static/app.css:1-9` (add `--color-warning`; add the dark-mode
  `@media`/`[data-theme="dark"]` blocks), `:323` (`.merge-badge` — hardcoded hex → token)
- Create: `src/fledermap/web/templates/_theme_init.html`
- Modify: `src/fledermap/web/templates/map.html:3-4` (include the new partial)
- Modify: `src/fledermap/web/templates/sessions_list.html:3-4` (include the new partial)
- Modify: `src/fledermap/web/templates/session_detail.html:3-4` (include the new partial)

**Interfaces:**
- Consumes: nothing new — the six existing `--color-*` tokens `app.css` already defines.
- Produces: `--color-warning` (new token, used by `.merge-badge`); the `data-theme` attribute
  contract on `<html>` (`"light"`, `"dark"`, or absent) that Task 2's toggle writes to and reads
  from; the `_theme_init.html` partial name, which nothing outside this task needs to know beyond
  "include it first in `<head>`, before `app.css`'s `<link>`".

- [ ] **Step 1: Add `--color-warning` and the dark-mode CSS blocks**

In `src/fledermap/web/static/app.css`, replace the `:root` block (currently lines 1-9):

```css
:root {
  --color-text: #1a1a1a;
  --color-muted: #666;
  --color-border: #d8d8d8;
  --color-bg: #ffffff;
  --color-bg-subtle: #f7f7f8;
  --color-accent: #2b6cb0;
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
```

with:

```css
:root {
  --color-text: #1a1a1a;
  --color-muted: #666;
  --color-border: #d8d8d8;
  --color-bg: #ffffff;
  --color-bg-subtle: #f7f7f8;
  --color-accent: #2b6cb0;
  --color-warning: #b7791f;
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}

/* Dark mode: system preference by default; an explicit override (set on
   <html data-theme="light|dark"> by _nav.html's toggle, applied before
   first paint by _theme_init.html's inline script) wins in either
   direction. See docs/style-guide.md's "Dark mode" section.
   data-theme is only ever "light" or "dark" when present -- absent
   entirely means "follow system", which is what the
   :not([data-theme="light"]) guard below relies on: without it, an
   explicit light override couldn't defeat a dark system preference. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --color-text: #e8e8e8;
    --color-muted: #9a9a9a;
    --color-border: #3a3d42;
    --color-bg: #1a1a1a;
    --color-bg-subtle: #242628;
    --color-accent: #5b9bd5;
    --color-warning: #d99a3f;
  }
}
:root[data-theme="dark"] {
  --color-text: #e8e8e8;
  --color-muted: #9a9a9a;
  --color-border: #3a3d42;
  --color-bg: #1a1a1a;
  --color-bg-subtle: #242628;
  --color-accent: #5b9bd5;
  --color-warning: #d99a3f;
}
```

Then find `.merge-badge` (currently `app.css:323`):

```css
.merge-badge { color: #b7791f; font-size: 0.8rem; }
```

and change it to:

```css
.merge-badge { color: var(--color-warning); font-size: 0.8rem; }
```

- [ ] **Step 2: Create the anti-flash init partial**

Create `src/fledermap/web/templates/_theme_init.html`:

```html
{# src/fledermap/web/templates/_theme_init.html -- included first in every
   page's <head> (map.html, sessions_list.html, session_detail.html), before
   app.css's <link>. Applies a stored manual dark/light override BEFORE
   first paint -- deliberately inline (not a separate .js file, which would
   fetch over the network and run too late to avoid a flash of the wrong
   theme) and deliberately not deferred. No stored value, or an invalid one
   -- no attribute set, so app.css's plain @media (prefers-color-scheme:
   dark) block decides, same as if this script didn't run at all. Written
   by _nav.html's toggle; see docs/style-guide.md's "Dark mode" section. #}
<script>
  (function () {
    var theme = localStorage.getItem("fledermap-theme");
    if (theme === "light" || theme === "dark") {
      document.documentElement.dataset.theme = theme;
    }
  })();
</script>
```

- [ ] **Step 3: Include the partial in all three pages' `<head>`, before `app.css`**

In `src/fledermap/web/templates/map.html`, the `<head>` currently reads (lines 3-9):

```html
<head>
  <meta charset="utf-8">
  <title>Fledermap</title>
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='leaflet.css') }}">
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='MarkerCluster.css') }}">
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='MarkerCluster.Default.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
```

Change to:

```html
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  <title>Fledermap</title>
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='leaflet.css') }}">
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='MarkerCluster.css') }}">
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='MarkerCluster.Default.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
```

In `src/fledermap/web/templates/sessions_list.html`, the `<head>` currently reads (lines 3-7):

```html
<head>
  <meta charset="utf-8">
  <title>Fledermap — Sessions</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
```

Change to:

```html
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  <title>Fledermap — Sessions</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
```

In `src/fledermap/web/templates/session_detail.html`, the `<head>` currently reads (lines 3-8):

```html
<head>
  <meta charset="utf-8">
  <title>Fledermap — Session {{ detail.session.id }}</title>
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='leaflet.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
```

Change to:

```html
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  <title>Fledermap — Session {{ detail.session.id }}</title>
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='leaflet.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
```

- [ ] **Step 4: Run the Python checks**

```bash
hatch fmt --check
hatch run types:check
hatch test
```

Expected: all green, no diffs (this task touches no Python).

- [ ] **Step 5: Visual verification**

Two checks: a regression screenshot of the real pages in their default (light) state, and a
direct look at the dark-mode CSS block's actual colors via a tiny standalone fixture — headless
Chrome has no reliable command-line flag for emulating `prefers-color-scheme` (verified directly
in this environment: `--force-dark-mode --enable-features=WebContentsForceDark` darkens a page
with *no* dark media query at all, proving it's Blink's unrelated auto-dark-content heuristic,
not real `prefers-color-scheme` emulation — don't reach for that flag here or in Task 2), so the
fixture tests the CSS rule directly instead of trying to trick the browser into a system state.

Regression check — start the dev server (kill anything already on the port first, a known issue
in this environment per `CLAUDE.md`):

```bash
ss -ltnp 2>/dev/null | grep -E ':5001\s' || true
```

If that prints a PID still listening, `kill -9` it and re-check before continuing. Then, with
`dangerouslyDisableSandbox: true` (Docker/local-network access needs it here):

```bash
nohup hatch run fledermap serve > "$TMPDIR/fledermap-serve.log" 2>&1 &
sleep 3
curl -sf http://127.0.0.1:5001/ > /dev/null && echo "server up"
google-chrome --headless=new --disable-gpu --screenshot="$TMPDIR/dark-t1-map.png" \
  --window-size=1280,900 http://127.0.0.1:5001/
google-chrome --headless=new --disable-gpu --screenshot="$TMPDIR/dark-t1-sessions.png" \
  --window-size=1280,900 http://127.0.0.1:5001/sessions
```

Read both PNGs back (the Read tool displays images). Confirm both pages render **identically to
before this task** — same light colors, same layout. This task adds an inert script and unused
CSS blocks; nothing about the default appearance should change.

Dark-block check — build a standalone fixture that links the real `app.css` and forces the dark
override directly in its own markup (no JS, no browser flags needed):

```bash
cat > "$TMPDIR/dark-fixture.html" <<'EOF'
<!doctype html>
<html data-theme="dark">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="http://127.0.0.1:5001/static/app.css">
</head>
<body style="margin:0; padding:2rem; display:flex; flex-direction:column; gap:1rem;">
  <div style="padding:1rem; border:1px solid var(--color-border); background:var(--color-bg-subtle);">
    bg-subtle panel, <span style="color:var(--color-muted);">muted text</span>,
    <a href="#" style="color:var(--color-accent);">an accent link</a>
  </div>
  <p class="merge-badge">a merge-badge warning, should read in --color-warning</p>
</body>
</html>
EOF
google-chrome --headless=new --disable-gpu --screenshot="$TMPDIR/dark-t1-fixture.png" \
  --window-size=600,300 "file://$TMPDIR/dark-fixture.html"
```

Read `dark-t1-fixture.png` back. Confirm: page background and panel are dark grays (not pure
black), body text and the panel's own background are visibly distinct from each other, the link
is a legible light blue, and the merge-badge text is a legible amber/gold — matching the Task 1
Step 1 hex values (`--color-bg: #1a1a1a`, `--color-bg-subtle: #242628`, `--color-accent:
#5b9bd5`, `--color-warning: #d99a3f`). If any color looks wrong, fix `app.css` before proceeding.

This fixture file lives only under `$TMPDIR` — never commit it.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/static/app.css src/fledermap/web/templates/_theme_init.html \
        src/fledermap/web/templates/map.html src/fledermap/web/templates/sessions_list.html \
        src/fledermap/web/templates/session_detail.html
git commit -m "feat: dark-mode color tokens and CSS mechanism"
```

Use `dangerouslyDisableSandbox: true` for `git` too (this repo's convention — sandboxed git
config writes leave a stale `.git/config.lock`).

---

### Task 2: Manual toggle in the nav, style guide update

**Files:**
- Modify: `src/fledermap/web/templates/_nav.html` (extend the existing `x-data`, add the toggle
  button)
- Modify: `src/fledermap/web/static/app.css` (align the new button like `#sidebar-toggle`)
- Modify: `docs/style-guide.md` (Dark column on the token table, `--color-warning` row, new
  "Dark mode" section)

**Interfaces:**
- Consumes: the `data-theme` attribute contract and `_theme_init.html` from Task 1 (this task is
  the only writer of the `fledermap-theme` `localStorage` key `_theme_init.html` reads).
- Produces: nothing further consumes this — it's the last piece of the feature.

- [ ] **Step 1: Add the toggle to `_nav.html`**

Replace the whole file (currently 14 lines) with:

```html
{# src/fledermap/web/templates/_nav.html -- included by map.html,
   sessions_list.html, session_detail.html. Design spec section 8:
   expanded by default at normal widths (this is the app's only standing
   nav, so hiding it by default would just make /sessions harder to find);
   auto-collapses below a responsive breakpoint, matching the "columns
   stack on narrow screens" convention Phase 5a's drawer panels already
   use, plus a manual toggle at any width.

   Also owns the dark-mode toggle (2026-08-28): a single x-data scope for
   both, since they're both small pieces of standing nav state with no
   need to be split into separate components. theme starts from
   localStorage, not from document.documentElement.dataset.theme --
   _theme_init.html already applied that (before this component ever
   initializes) for the override case, and reading localStorage directly
   here is the one source of truth for what state to display, whether or
   not an override is currently active. #}
<nav id="sidebar"
     x-data="{
       collapsed: window.matchMedia('(max-width: 800px)').matches,
       theme: localStorage.getItem('fledermap-theme') || 'system',
       cycleTheme() {
         var next = { system: 'light', light: 'dark', dark: 'system' }[this.theme];
         this.theme = next;
         if (next === 'system') {
           localStorage.removeItem('fledermap-theme');
           delete document.documentElement.dataset.theme;
         } else {
           localStorage.setItem('fledermap-theme', next);
           document.documentElement.dataset.theme = next;
         }
       },
     }"
     :class="{ collapsed: collapsed }">
  <button type="button" id="sidebar-toggle" @click="collapsed = !collapsed" aria-label="Toggle navigation">☰</button>
  <button type="button" id="theme-toggle" @click="cycleTheme()" :aria-label="'Theme: ' + theme + ' -- click to change'">
    <span x-text="theme === 'dark' ? '🌙' : (theme === 'light' ? '☀️' : '🖥️')"></span>
  </button>
  <a class="sidebar-link" href="/"><span class="label">🦇 Map</span></a>
  <a class="sidebar-link" href="/sessions"><span class="label">Sessions</span></a>
</nav>
```

- [ ] **Step 2: Align the new button in `app.css`**

Find `#sidebar-toggle`'s rule in `src/fledermap/web/static/app.css`:

```css
#sidebar-toggle { align-self: flex-end; margin-bottom: 0.5rem; }
```

Change it to also cover `#theme-toggle`, matching the exact same treatment (right-aligned in the
column, same bottom margin, no separate rule needed):

```css
#sidebar-toggle, #theme-toggle { align-self: flex-end; margin-bottom: 0.5rem; }
```

- [ ] **Step 3: Update `docs/style-guide.md`**

Find the existing "Color tokens" table:

```markdown
| Token | Value | Use |
|---|---|---|
| `--color-text` | `#1a1a1a` | Primary body text |
| `--color-muted` | `#666` | Secondary/metadata text — labels, captions, timestamps |
| `--color-border` | `#d8d8d8` | Borders on inputs, panels, dividers |
| `--color-bg` | `#ffffff` | Page and control background |
| `--color-bg-subtle` | `#f7f7f8` | Panel/toolbar background, one step off white |
| `--color-accent` | `#2b6cb0` | Links and interactive accents |
```

Replace with a Dark column and the new token's row:

```markdown
| Token | Light | Dark | Use |
|---|---|---|---|
| `--color-text` | `#1a1a1a` | `#e8e8e8` | Primary body text |
| `--color-muted` | `#666` | `#9a9a9a` | Secondary/metadata text — labels, captions, timestamps |
| `--color-border` | `#d8d8d8` | `#3a3d42` | Borders on inputs, panels, dividers |
| `--color-bg` | `#ffffff` | `#1a1a1a` | Page and control background |
| `--color-bg-subtle` | `#f7f7f8` | `#242628` | Panel/toolbar background, one step off the page background |
| `--color-accent` | `#2b6cb0` | `#5b9bd5` | Links and interactive accents |
| `--color-warning` | `#b7791f` | `#d99a3f` | `.merge-badge`'s warning color |
```

Immediately after the "Color tokens" section (before "## Spacing rhythm"), add a new section:

```markdown
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
```

- [ ] **Step 4: Run the Python checks**

```bash
hatch fmt --check
hatch run types:check
hatch test
```

Expected: all green, no diffs (this task touches no Python).

- [ ] **Step 5: Visual verification**

Restart the dev server against this task's changes (kill the stale one from Task 1's verification
first):

```bash
ss -ltnp 2>/dev/null | grep -E ':5001\s' || true
```

If that prints a PID, `kill -9` it. Then, with `dangerouslyDisableSandbox: true`:

```bash
nohup hatch run fledermap serve > "$TMPDIR/fledermap-serve.log" 2>&1 &
sleep 3
curl -sf http://127.0.0.1:5001/ > /dev/null && echo "server up"
google-chrome --headless=new --disable-gpu --screenshot="$TMPDIR/dark-t2-nav.png" \
  --window-size=1280,900 http://127.0.0.1:5001/
```

Read the PNG back. Confirm the sidebar shows two icon buttons stacked near the top-right of the
nav column (☰ then a 🖥️/☀️/🌙 button), both with the same bordered-button look as the rest of the
app, right-aligned the same way.

The click-cycle, `localStorage` persistence, and no-flash-on-reload behavior need a real
interactive browser — headless screenshots can't drive a click or inspect `localStorage` without
new tooling this project doesn't have (a CDP client), which is disproportionate for a one-off
manual check. This is the same category of gap the project already accepts for `app.js` (zero
automated coverage, established precedent) — verify manually:

1. Open `http://127.0.0.1:5001/` in a real browser.
2. Click the theme button three times. Confirm it reads 🖥️ → ☀️ → 🌙 → 🖥️, and the page's colors
   switch to light / dark / back to system-driven each time.
3. With the button on 🌙 (dark, forced), reload the page. Confirm it stays dark immediately on
   load, with no visible flash of the light theme first.
4. Open the browser's devtools, confirm `localStorage.fledermap-theme` reads `"dark"` while the
   button shows 🌙, and that the key is absent entirely once cycled back to 🖥️ (system).
5. Repeat step 3's reload check on `/sessions` and a `/sessions/<id>` page — confirms the
   override persists across pages, not just within one.

If any step fails, fix the relevant file (`_nav.html`, `_theme_init.html`, or `app.css`) before
proceeding.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/templates/_nav.html src/fledermap/web/static/app.css \
        docs/style-guide.md
git commit -m "feat: dark-mode toggle in nav, style guide update"
```

---

## Final Verification

After both tasks:

```bash
hatch fmt --check
hatch run types:check
hatch test
```

All green, no warnings. Then a full manual pass in a real browser across all three pages
(`/`, `/sessions`, a `/sessions/<id>` detail page) in each of: system-light, system-dark (via
the browser's own devtools "Emulate CSS media feature prefers-color-scheme" — Chrome DevTools →
Rendering tab), forced-light override, forced-dark override. Confirm the map and session
mini-map never change appearance in any of the four states, and that every panel, form, and the
nav itself read correctly in both light and dark.
