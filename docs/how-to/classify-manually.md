# Classify a recording manually

Open a recording's own page (click its marker, then **Details** in the
drawer) to reach the **Classify** box below its spectrogram/oscillogram.
This is deliberately a separate, close-review page rather than something
you can do from the map drawer — correcting a species ID is worth a
proper listen first.

- Type into the search box to find a species or group by name or code,
  then click a result to add it as a chip. You can add more than one
  species to the same recording (a file with overlapping calls from
  different bats).
- **No ID** and **Noise** are their own buttons, not species — pick one
  when you're confident there's nothing more specific to say. They're
  mutually exclusive with species chips: adding a species while No
  ID/Noise is active clears it, and vice versa.
- **Clear** removes every manual chip and verdict on this recording at
  once, with no confirmation prompt — there's no way to undo it once
  clicked.

Every click saves immediately; there's no separate save button, and
nothing you do here is lost by navigating away.

## Can't tell the exact species?

Before reaching for No ID, search for **HiF**, **LoF**, or **Hilo** —
frequency-class groups (high-frequency, low-frequency, or a recording with
both call types at once) that are still a real, useful answer when the
exact species genuinely isn't identifiable by ear or eye, and are more
informative than a plain "I don't know."

## Why your correction stays put

A manual classification doesn't get silently outranked by whatever the
detector's own automatic classifier says on a later re-scan — see
[The classifier model](../explanation/classifier-model.md) for how
Fledermap decides which identification actually wins when several sources
disagree.
