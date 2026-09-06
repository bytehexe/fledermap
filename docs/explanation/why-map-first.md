# Why map-first?

Existing bat-recording tools are mostly spreadsheet-shaped: a table of
files, a spectrogram viewer, batch analysis. That's the right shape for
processing a big survey dataset, but it's the wrong shape for the simpler,
more common thing this project actually set out to do: make it genuinely
pleasant to revisit a night out with a detector — where you were, what you
heard, and when.

Location and time are how a night out is naturally remembered, so the map
is the primary view rather than a list. Clicking around the map to see
what turned up where is meant to feel like browsing a journal, not
querying a database.

That said, "pleasant to browse" and "honest data" aren't in tension here.
Every identification keeps its source and its own claim (see
[The classifier model](classifier-model.md)) rather than collapsing
straight to a single label, and recordings group into sessions and sites
rather than sitting as an unstructured pile of files — so the same data
that makes a map fun to click around also holds up if you later want to
treat it as real survey documentation.
