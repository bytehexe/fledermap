---
name: fledermap-style-guide
description: Use before writing or reviewing any Fledermap HTML/CSS (templates, app.css), and before drafting or reviewing any spec/plan that adds, redesigns, or re-places a UI element.
---

# Fledermap Style Guide

Before writing or modifying any template or CSS in `src/fledermap/web/`, read
`docs/style-guide.md` — it documents this project's established color tokens, spacing rhythm,
form-control styling, and shared classes (`.stacked-form`, `.filter-bar`). Match it; don't
invent new patterns for something the guide already covers.

**This also applies before drafting or reviewing a spec/plan**, not just when touching actual
HTML/CSS — a spec that adds, redesigns, or re-places a UI element must decide its placement
against `docs/style-guide.md` (see its "decide element placement once, project-wide" standing
rule) before the spec is written, not leave it for implementation to pick. Don't skip loading this
skill just because the file being written is a markdown spec, not a template.

If a change reuses a rule that today is written page- or ID-scoped for one element, promote it
to a shared class in the same change — don't leave a second copy sitting next to the first.
