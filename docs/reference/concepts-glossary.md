# Concepts glossary

- **Recording** — one audio file. Identified by the audio content itself
  (its `fmt`/`data` chunks), so re-identifying a file on the detector
  later — which renames it and rewrites its metadata — doesn't create a
  duplicate.
- **Identification** — one source's claim about a recording (a species, or
  a No ID/Noise verdict). Multiple sources can each have their own claim
  on the same recording at once; they never overwrite each other. See
  [The classifier model](../explanation/classifier-model.md) for how
  Fledermap picks which one wins.
- **Verdict** — what an identification actually asserts: a **species**, a
  **No ID** (a bat call, but not identifiable further), or **Noise** (no
  bat call at all). Both No ID and Noise are answers in their own right,
  not missing data.
- **Source** — where an identification came from: a specific field in the
  detector's own metadata (e.g. its GUANO or `wamd` auto-ID), or a manual
  correction made in Fledermap.
- **Taxon** — a species, genus, or broader group (e.g. a frequency-class
  group like HiF/LoF) that an identification can point at.
- **Session** — a group of recordings from one detector with no large time
  gap between them — roughly, one outing.
- **Site** — a place, derived by clustering the locations of
  species-identified recordings — roughly, a spot worth remembering, not
  every single GPS point.
