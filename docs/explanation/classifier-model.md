# The classifier model

A single recording can carry more than one opinion about what it is. Your
detector's own on-device classifier writes its guess into more than one
metadata field, and you can add a manual correction on top — Fledermap
keeps every one of these as a separate claim rather than overwriting
earlier ones, and then picks a single "current best" answer to actually
show you.

## Why claims from different sources coexist

The detector's GUANO metadata, its `wamd` metadata, and its filename
convention can all encode an auto-ID, and they don't always agree with
each other — encoding differences, or one field simply being staler than
another. Keeping all of them, tagged by where each came from, means a
disagreement is visible and diagnosable instead of silently picked for
you by whichever field happened to get read first.

## How the "current best" answer is picked

When several sources have a claim, one specific order decides which one
you actually see: your own manual correction first, then the detector's
on-device manual correction, then its automatic GUANO, `wamd`, and
filename-based guesses, in that order.

There's one important exception to "the first source in that order always
wins": an *automatic* classifier's own **No ID** is treated as if it
hadn't answered at all, and Fledermap keeps looking further down the
list for an actual answer. A manual **No ID**, on the other hand, always
wins outright — a human choosing "I can't tell" is a deliberate judgment
worth keeping, not the same kind of gap as a classifier shrugging.

## No ID, Noise, Unidentified, and Unmapped species — four different things

These are easy to conflate, but they mean genuinely different things:

- **No ID** — a source is confident this recording contains a bat call,
  but isn't confident enough to name a species.
- **Noise** — a source is confident this recording does *not* contain a
  bat call at all.
- **Unidentified** (a map filter option, not a verdict) — no source has
  made *any* claim on this recording yet, of any kind.
- **Unmapped species** — a source gave a specific species code, but that
  code isn't one Fledermap has a taxon for.

That last case is deliberate, not a bug: a code Fledermap can't
confidently map to a species resolves to *no answer* rather than a guess,
and shows up as **Unmapped species** in the map's Taxon filter for you to
review. A wrong mapping — confidently pointing at the wrong species — is a
worse failure than an honest gap, because a wrong answer looks just as
credible as a right one.
