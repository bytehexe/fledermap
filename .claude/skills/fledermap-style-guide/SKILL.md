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
