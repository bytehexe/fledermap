# Fledermap Phase 4 (Map) — Design

## 1. Scope

Phase 4 per the parent spec's phasing table (`docs/superpowers/specs/2026-08-23-fledermap-design.md`
§15): "Recordings and site circles on a Leaflet map with server-side filters. Noise
hidden by default." This is the project's first web surface.

Deliberately **excluded**, per the phasing table's own split of §9 into Phase 4 and
Phase 5: the bottom drawer, recording/site detail panels, `/sessions`, `/recordings/{hash}`,
`/taxa`, and the job status strip. Clicking a marker in Phase 4 shows a native Leaflet
popup built from data already in hand — no server round trip, no drawer. All of that is
Phase 5.

## 2. Deviations and clarifications from the parent spec

Two real gaps surfaced while brainstorming this phase — found by checking what the
parent spec assumes against what actually exists in the codebase, not assumed.

**P4-1: `Site.name`/`admin_path` are never populated.** The parent spec's `jobs/`
table (§4) lists a `geo` queue with a `name_site` job (poiidx reverse-geocoding), and
`Site.name`/`SiteNameCache`'s own model docstrings say they're "populated by Phase 3's
poiidx naming job" — but the Phase 3 actually built (media + jobs, merged 2026-08-25)
never included this job; poiidx naming isn't assigned to any phase. §9 specifies Sites
"labelled with the poiidx name," which doesn't exist yet.

**Resolution:** Phase 4 falls back to a rounded-coordinate label (or "Site #N") when
`Site.name` is `NULL` — which is every site until poiidx naming ships as its own later
task. Not blocking this phase on an unrelated external-service integration. The `geo`
queue and `name_site` job remain unscheduled; a future task should either build them or
retire the `SiteNameCache`/`geo`-queue plan explicitly.

**P4-2: "Current best" identification is specified but never implemented.** §5 says:
"Current best is a function in `services` — manual wins, else highest-priority
non-superseded source by configured order... Not a stored column." No such function,
and no configured order, exist anywhere in the codebase (confirmed by grep before this
brainstorm).

**Resolution:** the configured order, decided in this brainstorm, for the sources that
produce real data today:

```
MANUAL > EMT_MANUAL > EMT_GUANO > EMT_WAMD > EMT_FILENAME
```

Human correction (UI-entered, then on-device) always wins. Among the EMT's own
auto-ID sources, richer source format wins when the EMT reports the same call three
ways: GUANO (richest metadata) > WAMD (binary metadata) > filename fallback (no
confidence data at all). `BATDETECT2`/`BATTYBIRDNET`/`KALEIDOSCOPE` sort below all of
the above — they produce no real data yet (v2), so their exact position is unobserved
and revisable without migration (per §5: "not a stored column").

## 3. Module layout

```
src/fledermap/
  web/
    __init__.py
    app.py              # create_app() factory; registers api + views blueprints
    api/
      __init__.py
      geojson.py         # /api/recordings.geojson, /api/sites.geojson
    views/
      __init__.py
      map.py             # GET / -- renders the map shell
    templates/
      map.html
    static/
      vendor/            # git-ignored; populated by scripts/fetch_vendor_assets.py
      app.js             # filter form -> fetch() -> Leaflet layer update
      app.css
  services/
    current_best.py       # current_best_identification(recording) -> Identification | None
    map_query.py           # filtered Recording/Site queries shared by both GeoJSON endpoints
scripts/
  fetch_vendor_assets.py   # pinned-version + SHA-256 fetch of Leaflet/markercluster/HTMX/Alpine
```

`web/api` and `web/views` both import only from `services/` — never `store/` directly,
preserving the SPA-migration escape hatch the parent spec calls out in §4 ("Migrating
to a SPA means deleting `web/views`; the backend is untouched").

## 4. Backend framework: Flask

No backend web framework was chosen anywhere in the parent spec — only the frontend
approach (HTMX+Alpine, §9/§10) was settled. Decided in this brainstorm: **Flask**.

- Minimal and unopinionated; Jinja2 ships with it, matching the HTMX-fragment approach
  directly.
- No async/sync boundary to bridge — the existing `services`/SQLAlchemy code is
  synchronous throughout (Phases 1-3), and Flask's request model matches that without
  translation. FastAPI would have required either an async rewrite of `services/` or a
  sync-in-async escape hatch for no benefit at this phase's scale.
- No competing ORM/migration opinions to reconcile — unlike Django, which brings its
  own ORM and migration story that would sit awkwardly next to the already-chosen
  SQLAlchemy 2.0 + Alembic stack (parent spec §4), duplicating a decision already made.

New dependency: `flask`. New CLI command: `fledermap serve`, running Flask's
development server. A production WSGI front end (gunicorn, waitress, or similar) is a
later concern — out of scope for a phase whose exit criterion is "renders a map,"
not "is production-hardened."

## 5. Static assets: fetched, not vendored, not npm

Leaflet, Leaflet.markercluster, HTMX, and Alpine are JS/CSS dependencies with no
existing tooling in this project to manage them (no `package.json`, no Node.js
anywhere in the toolchain).

**Rejected: committing vendored copies to the repo.** Would put third-party binary/JS
blobs under version control for no benefit over fetching them at setup time.

**Rejected: loading from a CDN at runtime.** Makes a self-hosted app depend on an
external CDN being reachable — works against the entire premise of "self-hosted,"
and would silently break in an airgapped or CDN-blocked deployment.

**Rejected: npm as a fetch-only dependency manager (no bundler).** Standard and
well-trodden, but introduces Node.js/npm as a permanent second toolchain alongside
`hatch`, for no benefit over a much smaller local script — and sits oddly next to the
explicit reason HTMX+Alpine was chosen over a frontend framework in the first place
(avoiding frontend build tooling, per §10's tripwire framing).

**Chosen: `scripts/fetch_vendor_assets.py`.** A local script, matching this project's
existing `scripts/check_yaml.py`/`scripts/check_commit_msg.py` convention (small,
in-repo, no external dependency) and this session's established principle (recorded in
the user's global CLAUDE.md) of preferring a small local script over an external tool
when the check is simple and the concern is trust at a sensitive moment — here, "did I
get the exact bytes I pinned" for code that will run in every visitor's browser.
Downloads Leaflet, Leaflet.markercluster, HTMX, and Alpine at pinned exact versions,
verifying each against a hardcoded SHA-256, into `<static-root>/vendor/`.

**Where the fetched files live is configurable, unlike `media_root`.** `media_root`
(Phase 3) is required with no default, because it holds precious, potentially large,
backup-relevant generated data — an explicit operator choice is correct there. Vendor
assets are the opposite: small, regenerable, non-precious cache-like files, and
downloading them *into the installed Python package's own directory* is fragile for
two real deployment shapes:

- **A system-wide install** (pip/pipx into `site-packages`, or a distro package):
  that location is often not writable by whoever runs the fetch step, and mixes
  immutable code with mutable, environment-specific fetched output.
- **A Docker image with a read-only root filesystem** (`docker run --read-only`): the
  fetch must happen at image-build time (before the read-only flag applies at
  `docker run`), populating a path the running container only ever reads — which
  works with any location the build step can write and the runtime can read, but
  conflates code and fetched-artifact directories if that location is inside the
  installed package.

`FLEDERMAP_STATIC_ROOT` (new, **optional** env var, unlike `media_root`'s required
`FLEDERMAP_MEDIA_ROOT`): when set, `scripts/fetch_vendor_assets.py` writes there and
Flask's `static_folder` reads from there. When unset, defaults to
`platformdirs.user_cache_dir("fledermap")` — the same "guess a reasonable per-install
cache location" problem `platformdirs` exists to solve, which is a different problem
from `media_root`'s "an operator must deliberately choose where real data lives."
New dependency: `platformdirs`.

## 6. GeoJSON API

`GET /api/recordings.geojson?bbox=&from=&to=&taxon=&verdict=&source=&session=` and
`GET /api/sites.geojson` (same filter params where applicable), matching §9's API
section verbatim. Both:

- Call `services/map_query.py`'s filtered `select()` over `Recording`/`Site` — one
  shared filter-building function, not two independent ones, so a filter added later
  can't silently apply to only one layer.
- Exclude `verdict IN ('noise', 'no_id')` unless the caller explicitly asks for them
  (`verdict=noise`/`verdict=no_id`/`verdict=all`) — the default-hidden rule from §9,
  implemented as a query default, not a client-side filter (so a raw API consumer
  gets the same default a browser does).
- Serialize each row into a GeoJSON `Feature` via `store/geo.py`'s existing
  `decode_point()`, which already returns `(lon, lat)` — GeoJSON's own coordinate
  order, so no reordering needed.
- Recordings' `properties` include the `current_best_identification()` result
  (species name, source, verdict) for marker coloring and the click popup — computed
  per request, not stored, per §5's explicit "not a stored column" instruction.
- Cap at **2000 features** per response. This project's own established scale
  assumption (`services/ingest.py`'s `sweep_missing` docstring: "fine at journal
  scale, tens to low thousands") makes true server-side, zoom-aware clustering
  unnecessary at this phase — Leaflet.markercluster already declutters client-side
  (§9: "Cosmetic grouping — changes with zoom"). Over the cap, the response reports a
  `truncated: true` flag and the caller narrows filters; no partial-and-silent
  results.

## 7. Filter interaction: update layers in place, never swap the map

**The map is the one stateful widget in this whole page, and §10's tripwire #1 names
exactly this class of bug as the most likely first crack: "A fragment swap destroys
the map and we write JS to preserve it."** Phase 4's filter form is therefore
deliberately **not** wired through `hx-swap` on any element containing the map.

- The filter controls (date range, taxon, verdict, session, source) are a plain
  `<form>`, reactive via Alpine (`x-model` on each input), not HTMX.
- On any input change, a small vanilla JS function (`static/app.js`) issues a
  `fetch()` to both GeoJSON endpoints with the current filter values as query params,
  then clears and re-populates the *existing* Leaflet `L.geoJSON`/marker-cluster
  layer objects with the response — the `L.Map` instance itself is constructed once
  on page load and never touched again.
- **Initial load uses the identical code path**: the page's own JS fetches both
  endpoints once immediately after `L.map(...)` is constructed, with the default
  filter values (noise/no_id excluded) already selected in the form. No
  server-rendered inline GeoJSON and no separate "first paint vs. filtered update"
  logic to keep in sync.
- Marker click opens a native `L.popup()` populated entirely from that marker's
  GeoJSON `properties` (already fetched) — no HTMX fragment request, no server round
  trip, matching this phase's explicit exclusion of any drawer/detail view.

## 8. New services code

**`services/current_best.py`**
```python
def current_best_identification(recording: Recording) -> Identification | None:
    """Manual wins, else highest-priority non-superseded source (§5, resolution
    P4-2 above). Not stored -- computed on every call, so the ordering can change
    without a migration."""
```
Iterates `recording.identifications` (already eager-loaded, `lazy="selectin"" per
the existing model), filters `superseded_at is None`, and picks by the fixed
precedence list in §2 above.

**`services/map_query.py`**
```python
def filtered_recordings(
    session: OrmSession,
    *, bbox: tuple[float, float, float, float] | None,
    date_from: datetime | None, date_to: datetime | None,
    taxon_id: int | None, verdict: Verdict | None, session_id: int | None,
    source: IdSource | None,
) -> Sequence[Recording]: ...

def filtered_sites(
    session: OrmSession,
    *, bbox: tuple[float, float, float, float] | None,
    date_from: datetime | None, date_to: datetime | None,
) -> Sequence[Site]: ...
```
Shared by both `web/api/geojson.py` endpoints and (later, Phase 5) any server-rendered
page that needs the same filtered set — one definition of "what the current filters
mean," not duplicated per endpoint.

## 9. Testing

- **`current_best_identification`**: pure unit tests over constructed
  `Identification` lists (no DB) — precedence order, superseded rows excluded, empty
  list returns `None`.
- **GeoJSON endpoints**: `pytest.mark.db`, Flask's `app.test_client()` against a real
  Postgres, matching this project's existing DB-test pattern. Covers: default
  noise/no_id exclusion, each filter param, bbox filtering, the 2000-feature cap and
  its `truncated` flag.
- **`fetch_vendor_assets.py`**: the SHA-256 verification logic is tested against a
  fixture byte string, not a live network call (no network access assumed in CI/test
  runs) — the actual download path is exercised manually at setup time, same as
  `ffmpeg`'s presence is a setup-time, not test-time, concern.
- **No JS test framework introduced.** Consistent with the HTMX-over-framework
  decision staying test-light on the frontend: `app.js`'s filter-to-fetch-to-layer-update
  logic is exercised manually and via the Flask test client's rendered HTML (asserting
  the map shell, the `<script>` tags, and the filter form's fields are present) —
  not unit-tested in isolation.

## 10. Explicitly out of scope (this phase)

- The bottom drawer, recording/site detail panels (Phase 5, §9's "Detail" section).
- `/sessions`, `/recordings/{hash}`, `/taxa` views (Phase 5).
- The job status strip (Phase 5 — ingest is already asynchronous as of Phase 1, but
  visible progress has no home until then).
- Production WSGI serving, auth (both already excluded project-wide per the parent
  spec's §14 "v1 excludes").
- The `geo` queue / `name_site` poiidx job (P4-1) — unscheduled, not this phase's job.
- Photos, Leaflet.Photo (parent spec marks these v2 explicitly, §9).

## 11. Decisions

| # | Decision |
|---|---|
| P4-1 | `Site.name`/`admin_path` unpopulated (no phase ever built the `geo` queue's `name_site` job) — Phase 4 falls back to a rounded-coordinate label until poiidx naming ships as its own task. |
| P4-2 | `current_best_identification`'s configured order, never previously defined: `MANUAL > EMT_MANUAL > EMT_GUANO > EMT_WAMD > EMT_FILENAME`, with the unpopulated v2 ML sources sorting below all of the above. |
| P4-3 | Backend framework: Flask (no async/sync boundary to bridge against the existing synchronous `services`/SQLAlchemy code; no competing ORM/migration opinions vs. Django). |
| P4-4 | Static JS/CSS assets: a local fetch script with pinned versions + SHA-256 verification, not vendored-in-git, not CDN-at-runtime, not npm. |
| P4-5 | Vendor asset location is configurable (`FLEDERMAP_STATIC_ROOT`, optional, defaults via `platformdirs.user_cache_dir`) — unlike `media_root`, because these are small, regenerable, non-precious cache-like files, not operator-deliberate data, and downloading them into the installed package's own directory breaks under a system-wide install or a read-only-root Docker deployment. |
| P4-6 | Filters update the existing Leaflet layers in place via a plain fetch(), never via `hx-swap` on any map-containing element — directly targeting §10 tripwire #1 ("a fragment swap destroys the map"). |
| P4-7 | GeoJSON responses cap at 2000 features with a `truncated` flag rather than true server-side zoom-aware clustering — matches this project's established "tens to low thousands" scale assumption; Leaflet.markercluster already declutters client-side. |
| P4-8 | Marker click opens a native Leaflet popup from already-fetched GeoJSON properties — no server round trip, no drawer (that's Phase 5). |
