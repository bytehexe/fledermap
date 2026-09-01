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
| [NABat — List of Species Codes](https://www.nabatmonitoring.org/species-codes) | North American Bat Monitoring Program codes. | Lists **two codes per species**: a four-letter and a six-letter form (*Eptesicus fuscus* is both `EPFU` and `EPTFUS`). Both belong to one authority — see the note below before treating them as separate sources. |

> **Naming — there is no official name for these code systems.** Searched for one;
> none found. Each authority uses only descriptive terms for its own:
> NABat says "four-letter species code" / "six-letter species code" (its column
> headers), Wildlife Acoustics says "abbreviated codes" (its page title).
>
> Do **not** call them "alpha codes". Birds have a real
> [standardized 4- and 6-letter alpha code](https://www.birdpop.org/pages/birdSpeciesCodes.php)
> system in which the *four*-letter form comes from the **English** name
> (American Robin → `AMRO`). Both bat forms come from the **scientific** name:
> `EPFU` is genus-2 + species-2, `EPTFUS` genus-3 + species-3. Borrowing the bird
> term would describe them wrongly.
>
> **The six-letter forms coincide with Wildlife Acoustics'.** Every species
> checkable in both lists matches exactly: *Antrozous pallidus* `ANTPAL`,
> *Eptesicus fuscus* `EPTFUS`, *Euderma maculatum* `EUDMAC`.
>
> **Even so, keep them as separate sources** — `nabat4`, `nabat6`, and whatever
> holds the WA vocabulary. Folding the six-letter codes into the WA source
> because they currently agree would re-introduce precisely the universal-code-key
> assumption spec D10 exists to reject, only at smaller scale. The two registries
> are independently maintained, the construction rule is not injective, and
> nothing binds them to resolve a collision the same way. Duplicating ~30 rows
> costs nothing; a silent merge of two authorities' claims is not recoverable
> afterwards, because the row no longer records who said it.
>
> A taxon may hold several codes under one source — `uq_taxon_code` is
> `(source, code)`, not `(source, taxon_id)` — so `EPFU` and `EPTFUS` can both
> point at *Eptesicus fuscus*. Pinned by
> `test_one_taxon_may_carry_several_codes_from_one_source`.

## File formats

| Source | Authoritative for | Notes |
|---|---|---|
| [GUANO specification](https://github.com/riggsd/guano-spec) ([spec document](https://github.com/riggsd/guano-spec/blob/master/guano_specification.md)) | The open bat-acoustics metadata standard — the `guan` RIFF sub-chunk, its UTF-8 `Key: Value` layout, and the core field names. | What `src/fledermap/ingest/guano_read.py` implements. |
| [Wildlife Acoustics GUANO Metadata Namespace](https://www.wildlifeacoustics.com/SCHEMA/GUANO.html) | The vendor's own `WA|` -namespaced GUANO extension fields. | Vendor extensions to the standard above, not a competing format. |
| [guano-py](https://github.com/riggsd/guano-py) | Reference Python implementation of GUANO reading and writing. | Useful for cross-checking our parser's behaviour on edge cases. |
| [`wamd2guano.py`](https://github.com/riggsd/guano-py/blob/master/bin/wamd2guano.py) | **A reference decoder for the undocumented `wamd` chunk.** | Wildlife Acoustics never documented `wamd`; ours was decoded by hex-dumping real files (spec D18). Cross-checked against this independent implementation — see below. |

> **`wamd` cross-check — done, R1 substantially narrowed.** `src/fledermap/ingest/wamd.py`'s
> type IDs, derived from two simulator-generated sample files, were compared against
> `wamd2guano.py`'s `WAMD_IDS` table (fetched 2026-08-24). Five of seven IDs match
> exactly on both number and meaning: `0x01` model, `0x05` timestamp, `0x0b` auto_id,
> `0x0c` manual_id, and `0x06` (ours "position", theirs "gpsfirst" — same field, theirs
> is just more precise about it being the *first* GPS fix).
>
> Two are worth a closer look:
> - **`0x03`** — ours "app version", theirs "firmware". Different label, plausibly the
>   same slot used differently: on the samples this holds an app build string ("App
>   3.1.10"), not detector firmware, which fits the two files being simulator output
>   rather than real hardware. Watch this specifically on the first real-hardware
>   recording — it may carry an actual firmware version there instead.
> - **`0x04`** — ours "device", theirs "**prefix**". This looked like a real
>   disagreement (a filename prefix is a very different thing from a device name), but
>   our own sample data resolves it: the real decoded value at this offset is the
>   literal string `"iPhone Simulator"`, which cannot be a filename prefix (EMT
>   filenames use species codes — `EPTSER`, `NoID`, `NOISE` — as prefixes, never a
>   device name). "device" is the empirically better-supported reading for at least
>   this generation of the format; `wamd2guano.py`'s "prefix" label may reflect a
>   different firmware version or hardware line than these two samples.
>
> Both remaining questions are pinned to spec R1 rather than left loose here, and both
> resolve automatically the moment a real-hardware recording is ingested (phase 0b).

> **`wamd` cross-check, continued — real hardware arrived, 2026-08-26.** Both open
> questions above are now settled: `0x03` does hold a real firmware/app build string
> on real hardware too (`"App 3.1.10"`, same shape as the simulator), and `0x04`
> stayed `"Echo Meter Touch 2 Standard Android"` — a device name, confirming "device"
> over `wamd2guano.py`'s "prefix" reading.
>
> New finding this cross-check exists to catch: **this device's `0x06` (position)
> writes the wrong sign for longitude**, verified against real coordinates (Hannover,
> Germany — positive/east is correct) while the standard `guan` chunk's `Loc Position`
> field gets the same coordinate right in the same file. Checked against
> `wamd2guano.py` specifically to rule out a documented sign convention we might be
> missing: its GPS parser branches on format, and for the plain
> `WGS84,<lat>,<lon>,<elev>` layout this device uses (its own "EMTouch format" branch,
> as opposed to the `N`/`S`/`E`/`W`-suffixed branch it does negate) it takes the values
> as literal signed floats, same as `ingest/wamd.py`'s `_parse_position`. The reference
> decoder reads this field exactly the way we do and still gets the wrong hemisphere —
> this is the device's own bug, not a convention mismatch in either decoder. Detail and
> consequences in the design spec's R1 section.

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
  Open, unclaimed follow-up (2026-09-01): `services/site_naming.py`'s candidate-outside-the-site
  check reprojects into a local UTM CRS and calls `shapely.intersects()` client-side, because
  poiidx has no query-time "does this candidate intersect a given shape" capability of its own —
  only `poi.py`'s `coordinates` field (real polygon/line geometry for way/relation-sourced POIs)
  and `poiIdx.py`'s distance-based `get_nearest_pois`. A server-side `ST_Intersects` against the
  `geography` column would be both more correct (true geodesic intersects, no UTM zone-edge
  distortion) and cheaper than the client-side version. Deliberately not done as part of the
  Fledermap fix that needed it: poiidx is a real pinned PyPI dependency (SN-1), not an editable
  local one, so a poiidx change means a release cycle before the Fledermap fix could ship.
  **Confirmed bug in `poiIdx.py`'s `init_regions_by_shape`, found 2026-09-01 in production**
  (installed `poiidx==0.0.9`, not the local checkout — those can differ): `if buffer is not None:
  ... local_shape.convex_hull().buffer(buffer)`. `convex_hull` is a shapely *property*, not a
  method — calling it with `()` invokes whatever geometry it returns (a `Point`, typically) as if
  that were itself callable, raising `TypeError: 'Point' object is not callable`. This fires for
  **every** non-`None` `buffer` value, unconditionally — there is no workaround value, only
  omitting `buffer` avoids it. Fledermap's `services/site_naming.py` briefly passed `buffer=` (to
  fix the region-confinement gap described above) and broke every real `name_site_task` run the
  moment it merged to `main`; no test caught it because every Fledermap test mocks
  `poiidx.get_nearest_pois` rather than calling the real package (a real coverage gap, not just a
  poiidx one — worth an occasional real, non-mocked smoke check against a live `poiidx_bats_db`
  before merging anything that changes how poiidx is called). Fledermap reverted to never passing
  `buffer` (commit on `fix/poiidx-buffer-crash`, 2026-09-01) until poiidx ships a real fix
  (`local_shape.convex_hull.buffer(buffer)`, dropping the `()`) and Fledermap's pin moves to a
  version that includes it — at which point BOTH this bug note and the region-confinement gap
  above should be revisited together, since fixing one re-enables fixing the other.
- `../mkmapdiary` — the map-first presentation this project's UI is modelled on,
  and the source of the local-projection clustering approach.
