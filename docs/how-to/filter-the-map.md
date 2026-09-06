# Filter the map

The filter bar above the map narrows down which recordings show as
markers. Every filter applies live — no separate "apply" button — and the
current set of filters is reflected in the page's URL, so a filtered view
is something you can bookmark or share.

- **From / To** — restrict to recordings within a date range.
- **Taxon** — restrict to one species (or genus/group). Check **not** next
  to it to invert the filter and show everything *except* that taxon —
  useful for "what else is here besides the common species I already
  know about." The dropdown only lists taxa actually present in your
  data, plus an **Unmapped species** entry covering every species code a
  classifier emitted that Fledermap couldn't resolve to a taxon — see
  [The classifier model](../explanation/classifier-model.md) for why that
  happens and isn't a bug.
- **Session** — restrict to one detector outing, listed by its time range
  and detector.
- **Source** — restrict to identifications from one specific source (a
  particular EMT metadata field, or a manual correction) rather than
  whichever source Fledermap currently trusts most for that recording.
- **Verdict** — defaults to **Species only**, hiding No ID/Noise/unmapped
  recordings so the map isn't dominated by non-species clutter. Switch it
  to **No ID**, **Noise**, **Unidentified** (no identification exists at
  all yet), or **All** to see those too.
- **★ Favourites only** — recordings you've starred, from the map drawer
  or the recording-detail page.

Click a recording marker to open the drawer with that recording's
spectrogram, audio, and identifications. Click a site's circle instead to
open a site panel with a **Show only this site** button — a **Site filter
active** chip then appears next to the filter bar; click its **×** to
clear it.

## The sessions list

`Sessions` in the navigation is a separate, list-based view over the same
data, filterable by detector and date range, with an **Open merge
proposals only** checkbox for sessions Fledermap has flagged as likely
belonging together (e.g. two detectors overlapping in time near the same
place) and not yet resolved. Accepting or rejecting a proposed merge
happens on that session's own detail page.
