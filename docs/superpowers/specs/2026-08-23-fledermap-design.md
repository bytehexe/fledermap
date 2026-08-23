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

`/` map · `/sessions` + detail (edit kind and notes, resolve merge proposals) ·
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
- **Session folders are not session boundaries.** `Session_20130401_053030`
  contains files dated 2015-06-10 and 2015-06-23 — a folder named for 2013,
  holding two nights thirteen days apart. Derive sessions from timestamps only
  (D7); never parse folder names.

### R2 — Does the EMT re-encode audio on re-ID? *(needs real field files)*

`audio_hash` (D8) assumes re-ID rewrites **only** metadata and filename. If the
app re-encodes, the hash changes and the identity scheme needs a fallback.
Cannot be tested with bundled samples.

### R3 — Does GPS survive the app's export path? *(needs real field files)*

If `Loc Position` is stripped on export, the map has no data. Mitigated but not
solved by recordings-without-GPS being first-class.

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
| **0b · Revalidate** | **Blocked until real field recordings exist.** Re-run the R1 dump and settle, in order: (a) is `guan` present alongside `wamd`; (b) do filename and metadata timestamps agree — settles D17; (c) does position survive the app's export path — closes R3; (d) does re-running auto-ID leave the `data` chunk byte-identical — closes R2. **Until this passes, every field mapping is provisional** |
| **1 · Ingest core** | `fledermap ingest <dir>` populates `bats_db` idempotently. No web, no media. The `guan`-mutation hash test passes |
| **2 · Derivation** | Sessions and sites derive; clustering regression test passes at both latitudes. Still no web |
| **3 · Media + jobs** | Procrastinate running; spectrograms and ÷10 previews generated on ingest |
| **4 · Map** | Recordings and site circles on a Leaflet map with server-side filters. Noise hidden by default |
| **5 · Drawer + views** | Recording and site drawers, sessions list with notes and merge proposals, job status strip |
| **6 · Watcher** | Watched directory with the settle rule; a night dropped in by Syncthing appears unattended |

Phases 1 and 2 are deliberately headless — the data model is the risky part, and
it is far cheaper to get wrong before a UI depends on its shape.

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
