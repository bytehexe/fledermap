# Authoritative sources

Where the domain facts come from. Check here before hand-entering a species
code, a metadata key, or a chunk layout — and add to this list when you find a
new source rather than leaving it in a commit message.

Each entry says what the source is authoritative **for**. That matters more than
the URL: several of these overlap in subject and disagree in detail.

## Species codes and names

| Source | Authoritative for | Notes |
|---|---|---|
| [Wildlife Acoustics — Bat Auto-ID Supported Species and Abbreviated Codes](https://answers.wildlifeacoustics.com/r/en-US/Bat-Auto-ID-Performance-and-Supported-Species/Bat-Auto-ID-Supported-Species-and-Abbreviated-Codes) | The codes the **Echo Meter Touch and Kaleidoscope actually emit**. This is the vocabulary `taxa_eu.yaml` maps. | **Species-level only** — it defines no genus or group codes. `MYOSPP` was invented against this list and had to be removed. 31 European species; we currently map 10. |
| [NABat — List of Species Codes](https://www.nabatmonitoring.org/species-codes) | North American Bat Monitoring Program codes. | **A different vocabulary — do not mix.** NABat uses 4-character codes (`MYSE`, `MYSO`); the EMT European list uses 6 (`PIPPIP`, `EPTSER`). This is exactly why `taxon_code` is per-source (spec D10) rather than one universal key. |

## File formats

| Source | Authoritative for | Notes |
|---|---|---|
| [GUANO specification](https://github.com/riggsd/guano-spec) ([spec document](https://github.com/riggsd/guano-spec/blob/master/guano_specification.md)) | The open bat-acoustics metadata standard — the `guan` RIFF sub-chunk, its UTF-8 `Key: Value` layout, and the core field names. | What `src/fledermap/ingest/guano_read.py` implements. |
| [Wildlife Acoustics GUANO Metadata Namespace](https://www.wildlifeacoustics.com/SCHEMA/GUANO.html) | The vendor's own `WA|` -namespaced GUANO extension fields. | Vendor extensions to the standard above, not a competing format. |
| [guano-py](https://github.com/riggsd/guano-py) | Reference Python implementation of GUANO reading and writing. | Useful for cross-checking our parser's behaviour on edge cases. |
| [`wamd2guano.py`](https://github.com/riggsd/guano-py/blob/master/bin/wamd2guano.py) | **A reference decoder for the undocumented `wamd` chunk.** | Wildlife Acoustics never documented `wamd`; ours was decoded by hex-dumping real files (spec D18). This is an independent implementation to check our type IDs against — see the note below. |

> **`wamd` cross-check — open item.** `src/fledermap/ingest/wamd.py` derives its
> type IDs (`0x01` model, `0x03` app version, `0x04` device, `0x05` timestamp,
> `0x06` position, `0x0b` auto ID, `0x0c` manual ID) from two simulator-generated
> sample files. `wamd2guano.py` is an independent decoding of the same chunk and
> should be compared against it. Agreement would retire a real risk; disagreement
> would be worth knowing before real recordings arrive.

## Classifiers (additional identification sources, not yet built)

Per the design, species ID is not this project's problem — the EMT already does
it, and other classifiers are simply further `identification` rows (spec D9).

- [BattyBirdNET-Analyzer](https://github.com/rdz-oss/BattyBirdNET-Analyzer)
- [BattyBirdNET-Pi](https://github.com/rdz-oss/BattyBirdNET-Pi) — the single-station system that prompted this project
- BatDetect2 — no URL recorded yet; add one when it is next referenced

## Prior art

Surveyed before starting; neither does what Fledermap does.

- [batbox](https://github.com/parsingphase/batbox) — the closest match found. Abandoned since July 2021.
- [batlog](https://github.com/dvarrazzo/batlog)

## Related local projects

Not public; on this machine only.

- `../poiidx` — the user's PostGIS OSM POI index, used to name derived locations.
  **It drops and recreates all its tables on any schema or filter-config
  mismatch**, which is why Fledermap uses a separate database (spec D11).
- `../mkmapdiary` — the map-first presentation this project's UI is modelled on,
  and the source of the local-projection clustering approach.
