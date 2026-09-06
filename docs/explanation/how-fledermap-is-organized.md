# How Fledermap is organized

Fledermap works with three separate places, each with a different role.
Knowing which is which is what makes the setup settings (and what's safe
to delete or move) make sense.

## The archive: your recordings, untouched

The archive is your detector's own export folder — wherever it syncs its
`.wav` files into. Fledermap only ever *reads* from it: it never moves,
renames, or writes into a recording, or deletes one. Whatever backup story
you already have for that folder (a phone/cloud sync, a manual copy) is
still the only thing protecting those files — Fledermap doesn't add one.

## The database: everything Fledermap has figured out

Every identification, session, and site Fledermap derives from your
recordings lives in its PostgreSQL database — not in the archive itself.
The database is where the actual value of a long-running install
accumulates: your recordings are unchanging source material, but the
database is what turns them into a map you can browse and correct over
time (a manual species correction, a favourite, a session note).

## The media root: fully regenerable

Spectrograms, oscillograms, and audio previews are rendered from the raw
audio, then cached to disk under the media root so the map doesn't
re-render them on every view. Unlike the archive or the database, this
directory holds nothing that can't be recreated — deleting it and letting
Fledermap re-render everything loses you nothing but some CPU time.

## What actually happens when you add a recording

1. **Ingest** reads new files from the archive, extracts their metadata
   (species codes, GPS position, timestamp), and computes an identity for
   each recording that survives a later on-device rename or re-export.
2. **Derive** groups recordings into sessions (by detector and time gap)
   and clusters sessions into sites (by geographic proximity).
3. **Media rendering** produces the spectrogram, oscillogram, and audio
   preview for the map and recording-detail pages to actually show.

`fledermap worker` runs all three stages on its own, continuously, once
installed — see [Set up Fledermap](../how-to/setup.md).
