# Fledermap — design

**Date:** 2026-08-23
**Status:** design approved, not yet planned
**Name:** `fledermap`

---

## 1. Purpose

A self-hosted web service for organising bat recordings made in the field with a
handheld detector — initially a Wildlife Acoustics **Echo Meter Touch**. The
primary view is a map.

Success criterion, in the owner's words: **journal first, records-capable.** It
must be genuinely pleasant to browse after a night out, while keeping the data
model honest enough — provenance on every identification, sessions as
first-class — to grow into survey documentation without a rewrite.

Species identification is **not** this project's problem. The EMT already
performs auto-ID and writes the result into each file. Additional classifiers
(BatDetect2, BattyBirdNET) are treated as further *sources* of identification,
not as a thing to be built.

### Non-goals

Habitat classification. Call annotation for ML training. Being a general
bioacoustics platform. Replacing Kaleidoscope.

---

## 2. Prior art

Surveyed 2026-08-23. Nothing covers this.

| Tool | Verdict |
|---|---|
| [batbox](https://github.com/parsingphase/batbox) | **Closest by far** — Django, GUANO, map, species search, browser audio, tested against an EMT 2. Alpha by its author's own description, **last commit July 2021**, no site derivation. Validates the concept; the field is empty. |
| BattyBirdNET-Pi / acoupi | Stationary live-monitoring stations. Different shape entirely. |
| Kaleidoscope Pro | Has a Linux build, but it is batch analysis — table and spectrogram, not a map-first browsing tool. |
| BatExplorer / BatScope / bcAdmin / SonoBat | Windows/Mac desktop. |
| Whombat / BSG-BATS / BattyCoda | Annotation portals for ML training. Different job. |
| Chirpity | Good Linux UX, birds only. |
| iNaturalist | Public platform, not your dataset, mishandles ultrasonic. |

Components to build on rather than reimplement: **guano-py** (reference GUANO
implementation), **batogram** (spectrogram rendering), and locally
`mkmapdiary`'s `LocalProjection`.

---

## 3. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Journal first, records-capable | Owner's stated success criterion |
| D2 | Three layers: Recording → Session → Site. **No Encounter layer** | Deliberately chosen over a 4-layer model. Encounters stay *derivable* later from retained timestamp, position, taxon and provenance |
| D3 | Ingest is a library; watched directory is the first front end | Survives multi-GB nights with no upload machinery. Web upload and CLI become further callers |
| D4 | Derived media precomputed on ingest | Instant browsing; the job runner it needs is the same one classifiers will use |
| D5 | Python · FastAPI · PostgreSQL+PostGIS · HTMX+Alpine+Jinja · Leaflet | guano-py and the classifiers are Python; no cross-language boundary |
| D6 | Sites are **pure derived projection**, rebuilt wholesale, carrying **zero** durable state | Makes unstable cluster identity a non-problem: nothing can be lost |
| D7 | Sessions are **incremental, never renumbered** — the durable annotation layer | They carry notes, so they must be stable. Exact inverse of sites |
| D8 | Identity is `audio_hash` over `fmt ‖ data` chunks, not path or whole file | The EMT renames files on re-ID, and GUANO lives *inside* the RIFF |
| D9 | Identifications are a table, multi-source, with `source_version` | Sources coexist; re-running a newer model appends rather than destroys |
| D10 | `taxon` split from `taxon_code` | Codes are per-source vocabulary; groups (`Myotis sp.`, `Nyctaloid`) are not species |
| D11 | Two new databases: `bats_db` and `poiidx_bats_db` | poiidx drops and recreates its tables on any config change |
| D12 | Clustering in Python, on locally projected coordinates | Reuses proven `LocalProjection`; makes `eps` genuinely metres |

> **Why PostGIS if clustering moved to Python (D12)?** Only the *partitioning*
> step is Python. PostGIS still earns its place for the `geography` column type,
> spatial indexes backing the map's bbox queries, and the fact that poiidx
> mandates a PostGIS server regardless (D11). At this data volume — thousands of
> recordings, not millions — sklearn's DBSCAN is instant, and reusing code the
> owner already trusts beats a SQL window function carrying a units footgun.
| D13 | Bottom drawer with an internal column grid | Spectrograms are wide and short |
| D14 | Multi-tenancy not built, not foreclosed | Owner flagged it as a maybe |
| D15 | Photos deferred to v2, model shaped for them | Geotagged photos carry their own time and position, exactly like a WAV |
| D16 | Archive indexed in place; the owner owns it, ingest is read-only | With Syncthing as transport the watched directory is already a replica — a managed copy would be a third copy of multi-GB nights |
| D17 | Store `filename_at` and `metadata_at` separately; `recorded_at` is computed and re-derivable | The only evidence available is synthetic, and it disagrees with itself by 12 hours. Defer the judgement rather than bake a guess into ingest |

> **Unavoidable provisional default.** `recorded_at` is `NOT NULL` and sessions
> derive from it, so phase 2 cannot run without *some* rule. The default is
> `timestamp_source: filename`, chosen only because it is the reading that
> yields plausible bat activity times (21:54, 21:35) on the sole available data.
> **This is a config default flagged provisional, not decision D17 being made.**
> Phase 0b revisits it; changing it re-derives and does not re-ingest.
| D18 | Read both `guan` and `wamd`; prefer `guan`, fall back to `wamd` | The samples carry only `wamd`; the user guide claims real files carry both. Supporting both costs little and removes the dependency on an unverified claim |

---

## 4. Architecture

```
fledermap/
  domain/     dataclasses + code tables. No I/O, no DB, no framework.
  ingest/     tree walk → RIFF chunk parse → GUANO read → filename fallback
  store/      SQLAlchemy 2.0 + GeoAlchemy2 models, repositories, Alembic
  derive/     sessions from time gaps; sites via DBSCAN; naming via poiidx
  media/      spectrogram render, time-expansion transcode
  jobs/       Procrastinate tasks + worker entrypoint
  services/   use-case layer. Returns domain objects. Knows nothing about HTML.
  web/
    api/      JSON + GeoJSON endpoints
    views/    Jinja fragments for HTMX
  cli/        fledermap ingest | derive | worker | serve
  util/
    projection.py   LocalProjection, copied from mkmapdiary
```

**The invariant that keeps the HTMX escape hatch open:** `web/api` and
`web/views` both call `services`, never `store` directly. Migrating to a SPA
means deleting `web/views`; the backend is untouched.

**ORM: SQLAlchemy 2.0 + GeoAlchemy2 + Alembic.** Deliberately diverges from
poiidx's peewee. poiidx hand-rolled PostGIS support and has *no migrations by
design* because its database is a cache. Ours is real storage that must migrate
without data loss.

**Jobs: [Procrastinate](https://procrastinate.readthedocs.io/)** — Postgres-backed,
so no Redis and no second service.

| Queue | Concurrency | Contents |
|---|---|---|
| `media` | parallel | `render_spectrogram`, `make_preview` |
| `geo` | **1** | `name_site` — poiidx may block for minutes |
| `classify` | *(v2)* | `run_batdetect2(audio_hash, model_version)` |

**Python tooling is hatch**, per the machine's conventions. Never `pip`, never a
manual venv, never `PYTHONPATH`.

---

## 5. Data model

### recording — immutable facts about one file

```
id
audio_hash      bytea UNIQUE     -- sha256(fmt_chunk ‖ data_chunk)
path            text             -- current location, relative to archive_root; MUTABLE
recorded_at     timestamptz NOT NULL   -- computed from the two below, per config
filename_at     timestamptz NULL       -- parsed from ID_YYYYMMDD_HHMMSS.WAV
metadata_at     timestamptz NULL       -- GUANO Timestamp, or wamd type 0x05
geom            geography(Point,4326) NULL     -- NULL is first-class
loc_accuracy_m  real NULL
elevation_m     real NULL
samplerate_hz   int
duration_s      real
te_factor       int NULL
make, model, serial   text NULL
note            text NULL
session_id      fk session NULL
guano_raw       jsonb            -- every key we did not model, verbatim
ingested_at     timestamptz
missing_since   timestamptz NULL -- source file no longer present at path
```

`guano_raw` is the hedge against the unverified EMT field mapping: if the
auto-ID turns out to live under a `WA|` key, the data is already captured and the
fix is a migration, not a re-ingest.

Keeping `recorded_at`, `geom` and identification provenance on every row is
precisely what keeps **encounters derivable later** (D2).

### identification — multi-source, versioned, append-mostly

```
id
recording_id    fk
source          'emt.guano' | 'emt.filename' | 'batdetect2'
                | 'battybirdnet' | 'kaleidoscope' | 'manual'
source_version  text NULL        -- app version, or MODEL version
verdict         'species' | 'no_id' | 'noise'
taxon_id        fk taxon NULL    -- NULL unless verdict='species'
raw_label       text             -- what the source literally said
confidence      real NULL
first_seen_at   timestamptz
superseded_at   timestamptz NULL
```

- **`source_version` is non-negotiable.** Without it, re-analysis silently
  destroys the record of what an earlier model said.
- `raw_label` means an unmapped code still ingests faithfully and resolves
  later, instead of failing. Unmapped labels form a small review queue.
- `verdict` gives `NoID` and `NOISE` a home instead of sentinel taxa, making
  "hide noise by default" a clean predicate.
- **Current best** is a function in `services` — manual wins, else highest-priority
  non-superseded source by configured order. Not a stored column, so changing the
  rule needs no migration.

### taxon / taxon_code

```
taxon
  id, rank 'species'|'genus'|'group'
  scientific_name UNIQUE      -- 'Pipistrellus pipistrellus', 'Nyctaloid'
  common_name_de, common_name_en
  parent_id fk taxon NULL

taxon_code
  source, code, taxon_id      -- PK (source, code)
```

Seeded from the [Wildlife Acoustics abbreviated code list](https://answers.wildlifeacoustics.com/r/en-US/Bat-Auto-ID-Performance-and-Supported-Species/Bat-Auto-ID-Supported-Species-and-Abbreviated-Codes).

### session — the durable annotation layer

```
id, started_at, ended_at
kind        'stationary' | 'transect'      -- user-set
detector_key text                          -- (make, serial)
note, weather, effort
```

> **Note (2026-08-29):** `kind` was removed entirely — see
> `docs/superpowers/specs/2026-08-29-fledermap-identification-based-sites-design.md`.
> Site derivation, its only consumer, is now identification-based instead.

### site — a projection, not an entity

Truncated and rebuilt wholesale. `centroid, radius_m, recording_count,
first_at, last_at, name`.

The species breakdown shown in the site drawer (§9) is **computed in `services`
by joining through recordings**, not stored on the site — it needs per-taxon
counts, and it must respect the caller's active verdict filter. Storing it would
freeze it against one filter setting.

### site_name_cache

`(geohash, name, admin_path, fetched_at)` — keyed on rounded coordinates,
**survives site rebuilds**, so re-derivation never re-triggers a Geofabrik
download.

---

## 6. Ingest

`ingest.scan(root) -> Iterator[ScannedFile]` — pure, no database, no side effects.

1. **Probe** — extension plus RIFF magic.
2. **Chunk-parse in one streaming pass** — locate `fmt `, `data`, `guan`. Never
   load a whole file into memory.
3. **`audio_hash`** — sha256 over `fmt ‖ data`, streamed in blocks.
4. **GUANO → `RecordingMetadata`** via the mapping fixed by the spike (§11).
5. **Filename parse** — `ID_YYYYMMDD_HHMMSS.WAV`. Timestamp is the fallback when
   GUANO's is missing; the ID always becomes an `emt.filename` identification,
   cross-checking GUANO for free.

`services.commit_scan()` resolves **by `audio_hash`**:

| Situation | Action |
|---|---|
| Unknown hash | INSERT recording + identifications, enqueue media jobs |
| Known hash, same path | Refresh metadata if `guano_raw` differs; supersede changed identifications |
| Known hash, **new path** | Update `path`, log the rename. **No media regeneration** |
| Same path, new hash | File replaced — new recording; old row's path marked missing |

Row 3 is the re-ID case and the entire reason identity is the audio. The
operation is idempotent: run twice, nothing changes.

**Files are indexed in place, never copied.** The watched directory *is* the
archive. Paths are stored relative to `archive_root` so the archive can move.

**Watcher settle rule:** ignore files whose mtime is under ~30 s old, or which
carry a sync tool's temp marker. Syncthing and rsync expose partially-written
files; without this, truncated WAVs are ingested on the first night.

### Deletion and missing files

**Ingest is strictly read-only on the archive.** It never moves, renames or
deletes a source file. This is a hard property, not a default.

The owner keeps the canonical archive (D16). Deleting a source file is therefore
permanent — but the system **degrades rather than breaks**, because derived media
are copies:

| After a source WAV is deleted | Status |
|---|---|
| Map, markers, sites, filters, sessions, identifications | works — all DB |
| Spectrogram, ÷10 preview | works — ours, under `media/` |
| Re-rendering at different settings | lost |
| Running a classifier later | lost |
| Exporting the original | lost |

You keep the journal; you lose the ability to ever re-analyse.

**A vanished file sets `missing_since`. It never deletes the row** — that would
destroy manually entered identifications, and a missing file is usually an
accident rather than an intent. The recording stays browsable and is flagged in
the UI.

> **Guard against mass false positives.** An unmounted drive or a mid-sync
> Syncthing makes *every* file look deleted at once. If more than **10%** of
> known recordings disappear in a single scan, the sweep is **refused** and
> warns loudly rather than marking anything. Without this, one unmounted NAS
> silently flags the entire dataset.

Because identity is `audio_hash` (D8), a file that reappears at a *different*
path is recognised as the same recording and `missing_since` clears itself.

---

## 7. Derivation — two opposite rules

**Sessions — incremental, never renumbered.** Partition by detector
`(make, serial)`, split on gap > `session_gap` (default 6 h). A new recording
joins an existing session if it falls in that window, else starts a new one.

*Edge case:* a recording ingested out of order can **bridge** two sessions. This
raises a **merge proposal surfaced in the UI — never an automatic merge**. Both
sessions may carry notes, and silently concatenating field notes is data loss
noticed months later.

**Sites — rebuilt wholesale.** DBSCAN over stationary, GPS-bearing recordings,
on coordinates projected by `LocalProjection`, so `eps` is metres.

> **Note (2026-08-29):** superseded by
> `docs/superpowers/specs/2026-08-29-fledermap-identification-based-sites-design.md` —
> site membership is now identification-based (`Verdict.SPECIES` via
> `current_best_identification`), not stationary-session-based. Left as-is here for
> historical record.

> **Pitfall, pinned by a test:** `ST_ClusterDBSCAN` and naive DBSCAN take `eps`
> in the units of the coordinate system. On raw EPSG:4326 that is *degrees*, and
> a 75 m radius silently becomes ~8 km. Project first, always.

`LocalProjection` picks one UTM/UPS zone from the centroid, so a dataset
spanning more than ~6° of longitude distorts at the edges. Irrelevant for
regional surveys; document it, don't fix it.

Per site, `GeoCluster` supplies the summary: `mass_point` → circle centre,
`radius` → circle radius, `zoom_level` → the zoom-to button. **Note that
`GeoCluster` does not cluster** despite its name — it is a summariser over one
point set. DBSCAN partitions; `GeoCluster` describes.

Recordings with `cluster_id IS NULL` are not errors — they are one-off spots,
shown individually.

**Tuning is free.** `eps`, `minpoints` and `session_gap` are config, and site
rebuilding is idempotent. This is the payoff of D6.

---

## 8. Media

- `render_spectrogram` → WebP.
- `make_preview` → **time-expanded ÷10 Opus**. Nearly free: rewrite the WAV
  header samplerate to a tenth and encode. 256 kHz becomes 25.6 kHz, so a 45 kHz
  *Pipistrellus* lands at 4.5 kHz — audible, and it sounds like classic TE playback.

Keyed on `audio_hash` + `params_hash`, so a settings change invalidates and a
rename never does.

```
media/<hash[:2]>/<hash>/spectrogram-<params>.webp
                       /preview-<params>.opus
```

---

## 9. Web surface

### Map — three independent, individually toggleable layers

| Layer | Meaning |
|---|---|
| **Recordings** | One marker per recording, decluttered by Leaflet.markercluster. **Cosmetic** grouping — changes with zoom. Coloured by current-best taxon |
| **Sites** | Derived clusters as circles. **Semantic**, zoom-independent, labelled with the poiidx name |
| **Sessions** | Transect tracks; photos via Leaflet.Photo *(v2)* |

Markercluster bubbles and site circles must be **visually distinct** — otherwise
one reads as the other.

Filters (date range, taxon, verdict, session, source) apply server-side.
`verdict IN ('noise','no_id')` is **excluded by default**; it is most of a real night.

### Detail — a bottom drawer with an internal column grid

Full width, drag-resizable, collapsible. Chosen because spectrograms are wide
and short, and because a bottom drawer keeps the map's width — the axis you pan
across. Works on a phone in the field.

- **Recording:** spectrogram spans full width; three columns beneath —
  *Identifications* (widest; all sources, superseded rows struck through),
  *Recording* metadata, *Context* (session, site, previous/next in time).
- **Site:** no spectrogram, columns only — species breakdown, site stats with
  poiidx name and admin path, sessions that touched it.

Clicking a site **opens its drawer**; the drawer carries a **"show only this
site"** action. Clicking therefore always means *show me this*, and filtering
stays deliberate and undoable rather than a click that silently changes the map.

Same HTMX target, two fragments: `/recordings/{hash}/panel`, `/sites/{id}/panel`.
Columns stack on narrow screens.

### Views

`/` map · `/sessions` + detail (edit notes, resolve merge proposals) ·
`/recordings/{hash}` · `/taxa` · `/sites` (read-only) · **job status strip** —
ingest is asynchronous, and without visible progress a running import looks like
a broken app.

### API

`/api/recordings.geojson?bbox=&from=&to=&taxon=&verdict=` ·
`/api/sites.geojson` · `/api/recordings/{hash}` · media under `/media/`.
Feature count capped, degrading to cluster summaries at low zoom.

---

## 10. HTMX tripwires

HTMX+Alpine was chosen over a frontend framework knowingly. **Any two of the
following tripping means we stop and reassess.** Re-checked at the end of every
implementation phase, with the result stated explicitly, pass or fail.

1. **A fragment swap destroys the map** and we write JS to preserve it. *Most
   likely first crack — `hx-preserve` around a stateful widget is the classic sign.*
2. Client state must stay in sync across three or more independent fragments
   (viewport ↔ filters ↔ selection ↔ list).
3. Any single Alpine component passes ~150 lines, or two islands need a shared store.
4. The same interactive component is needed in two places with different data.
5. Role-conditional UI spread across more than a handful of templates.
6. Optimistic updates, offline behaviour, or realtime are required.

**Insurance already in place:** the `services` boundary (§4), and a real JSON
surface from day one because Leaflet consumes GeoJSON regardless.

---

## 11. Open risks

### R1 — EMT metadata mapping *(spike run 2026-08-23; STILL OPEN)*

Ran against the two app-bundled samples in `~/Bat Sessions/`. Result overturned
a core assumption.

**There is no GUANO chunk.** Chunk layout is `RIFF · WAVE · fmt · data · wamd`
— only Wildlife Acoustics' proprietary chunk. Both files: 256 kHz, 16-bit mono
PCM.

`wamd` structure, decoded and confirmed by arithmetic (entry sizes reconcile
exactly to the chunk length): repeated entries of `uint16 type · uint32 size ·
payload`.

| Type | Field | Example |
|---:|---|---|
| 0x00 | format version | `0x0001` |
| 0x01 | model | `Echo Meter Touch` |
| 0x03 | app version | `App 3.1.10` |
| 0x04 | device | `iPhone Simulator` |
| 0x05 | timestamp | `2015-06-10 09:54:54+0200` |
| 0x06 | position | `WGS84,42.346973,-76.48760,(null)` |
| 0x0b | **auto ID** | `EPTSER` |
| 0x0c | **manual ID** | `MYODAU` (present in one file only) |

**Confirmed:** auto ID and manual ID are separate fields at the device level
(D9). Position *is* present, contrary to expectation, as a comma-joined string
with a `WGS84` prefix and a literal `(null)` for missing elevation — parse
defensively.

**Cross-checked against an independent decoder (2026-08-24).** `guano-py`'s
`wamd2guano.py` gives its own `WAMD_IDS` mapping for the same chunk. Five of
seven fields above match it exactly, by both ID and meaning. `0x03` matches by
ID with a different label (`firmware` there, `app version` here) — plausibly
the same slot read differently depending on whether the writer is simulator or
real hardware. `0x04` also matches by ID but disagrees on label (`prefix`
there, `device` here); this table's own example value, `iPhone Simulator`,
settles it in this table's favour — that string cannot be a filename prefix
(EMT filenames prefix with a species code, never a device name). Full
reasoning in `docs/references.md`. Neither remaining question changes the
mapping above; both are open for phase 0b to confirm on real hardware.

**Measured during implementation (2026-08-23), refining the timestamp picture.**
Once the filename and `wamd` parsers both existed, the disagreement could be
quantified rather than eyeballed:

| File | Filename | `wamd` | Δ |
|---|---|---|---|
| EPTSER | 21:54:46 | 09:54:54+02:00 | 43192 s = 12.00 h |
| MYODAU | 21:35:47 | 09:36:43+02:00 | 43144 s = 11.98 h |

Neither is a clean twelve hours. Add 12 h to each metadata time and it lands a
few seconds *after* the filename time — 8 s and 56 s respectively. The species
codes, by contrast, agree exactly between the two sources.

Read that as: **filename ≈ trigger time, metadata ≈ file-write time, plus a
12-hour AM/PM formatting fault in the writer.** That is evidence *for* the
provisional `timestamp_source: filename` default (D17), since the filename is
the one that marks when the bat actually flew — but it is evidence from a
simulator, so phase 0b must confirm the same relationship on a real device.

**Why R1 is not closed:** device is `iPhone Simulator`, so these were generated
on a developer's Mac, not recorded in the field. The metadata is also internally
inconsistent — a `+0200` offset against New York coordinates, which would be
−0400 in June. Synthetic fixtures cannot establish what a real device writes.

**Consequences for the design:**

- A **`wamd` reader is required**, not merely a GUANO reader. Whether real EMT2
  files also carry `guan` — as the user guide states — remains unverified.
  Build both, prefer `guan` when present, fall back to `wamd`.
- **Timestamp precedence is deferred, not guessed (D17).** The filename says
  `21:54:46`; `wamd` says `09:54:54` — twelve hours and eight seconds apart.
  Metadata-as-authoritative would place both recordings at ~09:54 in the
  morning, which is not when bats fly; filename-as-authoritative gives 21:54 and
  21:35, plausible for European summer. But the source is synthetic, so neither
  reading is evidence about real devices. Both are therefore stored —
  `filename_at` and `metadata_at` — with `recorded_at` computed per config and
  **re-derivable without re-ingesting**. A disagreement beyond a few seconds is
  flagged on the recording.

  > Changing the precedence rule after data exists can move recordings across
  > session boundaries, and sessions carry durable notes (D7). So a rule change
  > raises a **session re-derivation proposal**, never a silent rewrite — the
  > same principle as the bridging-recording merge.

- **D17 has a second half, found during implementation: *which zone?*** The
  filename encodes a wall-clock reading with no offset, so turning it into an
  absolute instant requires one, and any choice fabricates. Three facts, all
  from the real samples:

  1. The filename is naive: `2015-06-10 21:54:46`.
  2. The `wamd` metadata says `+02:00` — the only offset evidence in the file.
  3. **The position says otherwise.** 42.346973 / −76.48760 is in the US
     Eastern zone, UTC−4 in June. So `+02:00` is wrong *for where the recording
     claims to be*, which is further evidence these are simulator files.

  Rules: `recorded_at` borrows the offset from `metadata_at` when one is
  available, because it is the only evidence present and because
  `_disagreement_seconds` already normalises that way — the two must not make
  contradictory assumptions about the same instant. When no source carries an
  offset, a configured `default_timezone` applies (default UTC), and that case
  is a documented fabrication rather than a silent default.

  **Phase 0b gains a fifth question:** on a real recording, does the metadata
  offset agree with the GPS position's civil zone? If it does, position-derived
  zone lookup becomes the better long-term answer and this rule can be retired.
- **Session folders are not session boundaries.** `Session_20130401_053030`
  contains files dated 2015-06-10 and 2015-06-23 — a folder named for 2013,
  holding two nights thirteen days apart. Derive sessions from timestamps only
  (D7); never parse folder names.

**Phase 0b, 2026-08-26: first real field recordings arrived** (`Session_20260826_173533`,
10 files, Echo Meter Touch 2 *Standard Android* — a different device/OS than the
iPhone Simulator samples above). None contain an actual bat call (9× the device's own
"No ID" verdict, 1× no auto-ID attempt at all), but every phase-0b sub-question below
concerns the metadata/timestamp/position *plumbing*, not species content, so all but
one are answerable without a real bat call:

- **(a) `guan` alongside `wamd`? YES**, on this device — unlike the simulator samples,
  every real file carries both chunks. Uncovered a real parser bug in the process:
  `guan`'s chunk size is 605 (odd), and this device does **not** write the RIFF spec's
  pad byte after an odd-sized chunk — `ingest/riff.py`'s `iter_chunks` assumed every
  odd-sized chunk was followed by one, desyncing all chunk parsing after it and making
  the real `wamd` chunk invisible under its own name. **Fixed** (peeks at the would-be
  pad byte and only consumes it if it's actually `\x00`; `test_odd_sized_chunk_without_pad_byte_is_still_found`
  pins it). This closes (a).
- **(b) filename vs. metadata timestamps agree? YES**, exactly — e.g.
  `NoID_20260826_173535.wav`'s filename reads `17:35:35`; both `guan` and `wamd` read
  `2026-08-26 17:35:35+0200`. No trace of the simulator's 12-hour AM/PM fault. Since
  the two sources agree bit-for-bit on wall-clock reading here, D17's filename-vs-metadata
  precedence choice is moot for this device — either default produces the same
  `recorded_at`. Closes (b) for this device; still worth confirming on other
  hardware/OS combinations before treating the precedence question as universally moot.
- **(e) does the metadata offset agree with the GPS position's civil zone? YES.**
  `+02:00`, and the position (52.395°N, 9.740°E) is Hannover, Germany, which is CEST
  (UTC+2) in late August — consistent, unlike the simulator's `+02:00` against New York
  coordinates. Per this section's own text above: *"if it does, position-derived zone
  lookup becomes the better long-term answer and this rule can be retired."* That
  condition is now met on real hardware — worth a deliberate decision (not made here)
  on whether to switch D17's zone rule over, or gather more devices/locations first
  before generalising from one.
- **(d) re-ID hash stability — closed.** This is R2 below in full; noted here only to
  flag that it never depended on species content — an on-device manual correction of a
  "No ID" file to "Noise" gave the before/after pair, no real bat needed.

**New finding, not in the original R1 checklist: `wamd`'s own position field has a
real longitude sign bug on this device.** For every one of the 10 files, `guan`'s
`Loc Position` and `wamd`'s position type (`0x06`) describe the same physical spot but
disagree in longitude's sign — e.g. `Loc Position: 52.3954537 9.7402683` (GUANO) against
the literal wamd bytes `WGS84,52.3954537,-9.74027,102.19999694824219` (wamd) for the
identical recording. Verified at the raw-byte level, not a parsing artifact on our
side, and cross-checked against `wamd2guano.py` (the reference decoder for this
undocumented chunk, docs/references.md) to rule out a documented sign convention we
might be missing: its GPS parsing branches by format, and for the exact
`WGS84,<lat>,<lon>,<elev>` layout this device uses (its own "EMTouch format" branch,
as opposed to the `N`/`S`/`E`/`W`-suffixed branch it applies a sign correction to) it
takes the values as literal signed floats with **no** negation — identical to what our
own `_parse_position` already does. So the reference implementation reads this exactly
the way we do and still lands on the wrong hemisphere: the device itself writes the
wrong-signed value into its proprietary chunk while getting the standard GUANO field
right, not a convention either decoder is missing.

Currently harmless: `merge_metadata` already prefers GUANO's position over wamd's
unconditionally (`test_position_prefers_guano_over_wamd`), and every real file so far
carries both. It would matter only for a recording with a `wamd` chunk and no `guan`
chunk — not observed on this device, but no design principle rules it out for some
other EMT variant. Flagging rather than fixing: there is no way to *correct* wamd's
sign without already trusting GUANO or GPS-fix corroboration to know which hemisphere
is right, so any fix would just be "distrust wamd's longitude entirely," which is a
real decision, not a bug patch.

**R1 status: closed for the Echo Meter Touch 2, Android.** The original spike's `wamd`
field mapping (types 0x00–0x0c) matches real hardware output exactly, byte for byte.
Untested: an iOS device's real output, and any hardware running firmware old enough to
predate the GUANO-chunk addition seen here.

### R2 — Does the EMT re-encode audio on re-ID? *(closed, 2026-08-26)*

`audio_hash` (D8) assumes re-ID rewrites **only** metadata and filename. If the
app re-encodes, the hash changes and the identity scheme needs a fallback. **Closed**:
after the initial ingest above, `NoID_20260826_174531.wav` was manually reclassified
to "Noise" on-device and re-synced, arriving as `NOISE_20260826_174531.wav` — 22 bytes
larger (metadata growth: `guan` 605→614 bytes, `wamd` 163→170 bytes), auto-ID
untouched at `"No ID"` in both chunks, a new `manual_id` of `"NOISE"` added to both
(a *third* spelling of the sentinel, this time no space — `sentinel_verdict`'s
whitespace/case-insensitive match already covers it with no extra code). The already-ingested
DB row's stored `audio_hash` for the original file —
`0bb4fb84a7624cfa6617714d5df79d112dabbca389adec562f90d165f8c52262` — is a **byte-for-byte
match** against `audio_hash()` recomputed from the renamed file. Not a prefix guess: the
full 64-character digest, computed independently before and after the on-device edit,
is identical. D8's foundational assumption holds on real hardware.

### R3 — Does GPS survive the app's export path? *(closed, 2026-08-26, for this device/pathway)*

If `Loc Position` is stripped on export, the map has no data. Mitigated but not
solved by recordings-without-GPS being first-class. **Closed for the Echo Meter Touch
2 (Android) → Syncthing pathway**: all 10 real files carry plausible, present GPS data
in both `guan` and `wamd` — nothing was stripped. Scope is narrow, though: one
device, one OS, one export/sync mechanism. A web-upload front end or a different
device model reaching the archive by a different path is untested.

---

## 12. Constraints and hazards

**poiidx destroys its own database on config change.** `init_if_new()` hashes
every model's DDL plus the filter config; any mismatch **drops and recreates all
tables**. Therefore:

```
postgres
 ├─ poiidx_db        ← owner's existing index. NOT OURS. Never point fledermap at it
 ├─ poiidx_bats_db   ← ours. Regenerable; wiping costs only a re-download
 └─ bats_db          ← ours. Real storage, migrated, never dropped
```

This must be commented at the connection site, not only here.

**poiidx queries can block for minutes.** Every public query calls
`init_regions_by_shape()` first, lazily downloading and parsing a Geofabrik
`.pbf`. **Never call poiidx from a request handler** — only from the `geo` queue.
A freshly derived site renders unnamed briefly and is named asynchronously.

**Site naming targets toponymy, not habitat.** The filter's job is *give this
coordinate a name a human recognises* — quarters, suburbs, villages, named parks
and woods. Nearest named POI within *N* m preferring poiidx's `rank`, falling
back to `get_administrative_hierarchy_string()`.

**The visual companion server must run unsandboxed.** Started inside the command
sandbox it dies with the call and binds inside the sandbox's network namespace,
unreachable from the browser. Applies to any local dev server started from an
agent session.

---

## 13. Testing

- **Fixtures are synthesised** with guano-py — tiny WAVs carrying exactly the
  metadata each test needs. Deterministic, no multi-MB binaries in git, no
  redistribution of Wildlife Acoustics' audio. The two real samples are a
  separate, manually-run sanity check.
- **The load-bearing test: mutate the `guan` chunk, assert `audio_hash` is
  unchanged.** That is D8 tested directly.
- **Clustering regression:** identical point geometry at 50°N and 70°N must
  yield identical clusters. Pins the projection pitfall (§7).
- **Mass-disappearance guard:** a scan where >10% of known recordings are absent
  must mark *nothing* and raise. Cheap to write, and the failure it prevents —
  one unmounted NAS flagging the whole dataset — is silent and wide.
- Also: chunk parser, filename parser, session gap and merge-proposal logic,
  code→taxon resolution including unmapped labels, ingest idempotency, and that
  ingest never writes to `archive_root` (assert on a read-only mount).
- Database tests via **testcontainers + postgis**, matching poiidx's approach.

---

## 14. v1 excludes

Auth and multi-user (possible, not built) · photos (v2, model shaped for them) ·
BatDetect2/BattyBirdNET (queue slot exists, no integration) · encounters
(derivable from retained data) · transect track rendering · report exports ·
manual site drawing · web upload and CLI ingest front ends.

---

## 15. Suggested phasing

Scoped so each phase ends somewhere real rather than half-built. The HTMX
tripwires (§10) are re-checked at each boundary.

| Phase | Ends when |
|---|---|
| **0 · Spike** | *Run 2026-08-23. Produced the `wamd` mapping (R1), but could not close it — the only samples available are synthetic* |
| **0b · Revalidate** | **Blocked until real field recordings exist.** Re-run the R1 dump and settle, in order: (a) is `guan` present alongside `wamd`; (b) do filename and metadata timestamps agree — settles D17; (c) does position survive the app's export path — closes R3; (d) does re-running auto-ID leave the `data` chunk byte-identical — closes R2; (e) does the metadata's UTC offset agree with the GPS position's civil zone — settles D17's zone half. **Until this passes, every field mapping is provisional** |
| **1 · Ingest core** | `fledermap ingest <dir>` populates `bats_db` idempotently. No web, no media. The `guan`-mutation hash test passes |
| **2 · Derivation** | Sessions and sites derive; clustering regression test passes at both latitudes. Still no web |
| **3 · Media + jobs** | Procrastinate running; spectrograms and ÷10 previews generated on ingest |
| **4 · Map** | Recordings and site circles on a Leaflet map with server-side filters. Noise hidden by default |
| **5 · Drawer + views** | Recording and site drawers, sessions list with notes and merge proposals, job status strip |
| **6 · Watcher** | Watched directory with the settle rule; a night dropped in by Syncthing appears unattended |

Phases 1 and 2 are deliberately headless — the data model is the risky part, and
it is far cheaper to get wrong before a UI depends on its shape.

**Phase 6 should evaluate removing (or at least gating) the manual `ingest ARCHIVE`
and `worker ARCHIVE` commands.** Both take the archive root as a bare operator-typed
argument with nothing to check it against a prior invocation (`ingest` stores
`Recording.path` relative to whatever root it's given; `worker` resolves that same
relative path against whatever root *it's* given — see the Phase 3 media-jobs design,
§9, for why `worker` cannot fall back to a throwaway root the way `derive` does).
Nothing currently detects the two roots disagreeing. Two failure shapes follow from
that, and they are not equally survivable: a wrong-but-nonexistent-at-that-relative-path
root just makes `worker`'s task raise `FileNotFoundError` and fail loudly after
retries; a wrong-but-*plausible* root — e.g. a stale checkout, a sibling copy, a typo
that still resolves — resolves the relative path to a different real file and renders
its spectrogram/preview as if it belonged to the wrong recording, with no error at
all. Once the watcher owns one authoritative `archive_root` per running instance,
keeping `ingest`/`worker` around as parallel, independently-rooted entry points
re-opens exactly that risk for no remaining benefit — decide at Phase 6 design time
whether to remove them outright, restrict them to the watcher's own configured root,
or keep them for deliberate maintenance use (e.g. a full re-render after a media
params change) behind an explicit consistency check. `enqueue-media` is unaffected:
it never opens a source file, so it never resolves a path against `archive_root` at all.

## 16. Settled since first draft

1. **Name — `fledermap`.** The earlier working name `batlog` was dropped for
   collisions, all found before any code existed:
   - **BatLog** (Yanga et al., *Integrative and Comparative Biology*, 2026) — an
     open-source Arduino PIT-tag logger for bat behavioural studies that
     explicitly integrates acoustic monitors. Same domain, published, citable.
   - **BATLOGGER** (Elekon) — a commercial bat detector line with BatExplorer.
   - [`batlog`](https://github.com/dvarrazzo/batlog) — a Linux battery logger
     holding the obvious repo and PyPI name.

   Known near-neighbour, accepted: **Fledermaus** (QPS), 3D bathymetry and
   topography visualisation. Different field, different word.

2. **Licence — MIT.** Compatible with every planned dependency (FastAPI,
   SQLAlchemy, Procrastinate MIT; Leaflet BSD-2; markercluster MIT) and with
   batbox, the nearest prior art. Chosen knowingly against the owner's
   PolyForm Noncommercial default on mkmapdiary and poiidx: MIT permits a
   third-party hosted commercial version, and `LocalProjection`, copied from
   mkmapdiary, becomes MIT-available to anyone taking it from this repository
   even though mkmapdiary's own copy stays noncommercial. The owner holds
   copyright to both, so the relicensing itself is unproblematic.

3. **Repository.** Initialised at `~/projekte/bats`, branch `main`, with
   `commit.gpgsign` set repo-locally to match mkmapdiary and poiidx.

Database names deliberately keep the `bats_` prefix (`bats_db`,
`poiidx_bats_db`) rather than tracking the application name — they describe the
data they hold, and survive a future rename.
