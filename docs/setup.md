# Setup

How to get Fledermap running standalone: a PostgreSQL/PostGIS database, the
CLI's own environment, and the config file that can replace most of the
`FLEDERMAP_*` env vars below. This is the local/standalone path — a Docker
deployment is future work and, per `CLAUDE.md`, configures through env vars
only.

## 1. Database

Fledermap needs a PostgreSQL database with the PostGIS extension. Any
PostgreSQL ≥ 13 with PostGIS ≥ 3 works; the test suite pins
`postgis/postgis:16-3.4` (see `tests/conftest.py`), so that combination is
the most exercised.

Create a role for Fledermap to connect as, then a dedicated database owned
by that role, then enable PostGIS in it. `createuser`/`createdb` are the
standard PostgreSQL client tools for the first two steps (thin wrappers
around `CREATE ROLE`/`CREATE DATABASE`, installed alongside `psql`);
enabling an extension has no dedicated CLI equivalent, so that step still
goes through `psql`:

```bash
sudo -u postgres createuser --pwprompt fledermap
sudo -u postgres createdb --owner=fledermap bats_db
sudo -u postgres psql -d bats_db -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

`sudo -u postgres` is needed because a fresh PostgreSQL install only trusts
the `postgres` OS user to connect without a password (peer authentication)
and only the `postgres` DB role can create roles/databases by default. Once
`fledermap`'s role exists, later connections use it directly, not `sudo`.

Equivalently, as plain SQL through `sudo -u postgres psql`:

```sql
CREATE ROLE fledermap WITH LOGIN PASSWORD 'changeme';
CREATE DATABASE bats_db OWNER fledermap;
\c bats_db
CREATE EXTENSION IF NOT EXISTS postgis;
```

Either way, the resulting connection string for `database_url` is:

```
postgresql://fledermap:<password>@localhost/bats_db
```

**Never point Fledermap at `poiidx_bats_db` or any other poiidx database.**
poiidx hashes its own schema and filter config on startup and **drops and
recreates all its tables** on any mismatch — pointing Fledermap's
`database_url` there would destroy poiidx's data the next time poiidx
starts. Fledermap owns `bats_db` exclusively (spec D11, and the warning at
the top of `src/fledermap/store/db.py`).

Fledermap builds its own schema — there's no separate migration step to run
by hand. Every CLI command that touches the database (`ingest`, `derive`,
`worker`, `serve`, `enqueue-media`) runs `alembic upgrade head` against
`database_url` on startup, creating or updating the schema as needed.

## 2. Configuration

Every setting below can be provided two ways, and **an environment variable
always wins over the config file** when both are set for the same setting —
deliberate, since a future Docker deployment configures purely through env,
and the config file exists for this standalone path, not to override a
container's env.

| Setting | Env var | Config file key | Required? | Default |
|---|---|---|---|---|
| Database connection | `FLEDERMAP_DATABASE_URL` | `database_url` | **yes** | — |
| Archive root(s) to scan | `FLEDERMAP_ARCHIVE_ROOTS` | `archive_roots` | **yes** | — |
| Derived media directory | `FLEDERMAP_MEDIA_ROOT` | `media_root` | no | platformdirs data dir |
| Vendor JS/CSS directory | `FLEDERMAP_STATIC_ROOT` | `static_root` | no | platformdirs cache dir |
| Timestamp source | `FLEDERMAP_TIMESTAMP_SOURCE` | `timestamp_source` | no | `filename` |
| Fallback timezone | `FLEDERMAP_DEFAULT_TIMEZONE` | `default_timezone` | no | `UTC` |
| Session gap (hours) | `FLEDERMAP_SESSION_GAP_HOURS` | `session_gap_hours` | no | `6.0` |
| Site clustering radius (metres) | `FLEDERMAP_SITE_EPS_M` | `site_eps_m` | no | `75.0` |
| Site minimum points | `FLEDERMAP_SITE_MIN_POINTS` | `site_min_points` | no | `3` |
| Session-kind GPS-spread threshold (metres) | `FLEDERMAP_TRANSECT_DISTANCE_M` | `transect_distance_m` | no | `150.0` |
| poiidx database connection | `FLEDERMAP_POIIDX_DATABASE_URL` | `poiidx_database_url` | no | unset — site naming disabled |
| Site-naming search radius (metres) | `FLEDERMAP_SITE_NAMING_RADIUS_M` | `site_naming_radius_m` | no | `300.0` |
| `serve`'s interface | `FLEDERMAP_HOST` | `host` | no | `127.0.0.1` |
| `serve`'s port | `FLEDERMAP_PORT` | `port` | no | `5000` |

`archive_roots` (the directory or directories `ingest` scans) is a persistent
setting like every other row above, not a CLI argument — `ingest`/`worker`
take no positional `ARCHIVE` any more. It accepts more than one directory,
scanned in order (design spec §2/§4): comma-separated for the env var
(`FLEDERMAP_ARCHIVE_ROOTS=/mnt/syncthing,/mnt/sdcard-dump`), or a native TOML
array in the config file. Order matters when the *same* recording (identical
audio content) is found under two different roots — it's attributed to
whichever root was scanned first. A same-relative-path collision with
*different* content across two roots is not a tie at all: it produces two
separate recordings, one per root, distinguished by which root each was
found under.

Every path-typed setting here (`archive_roots`, `media_root`, `static_root`,
`FLEDERMAP_CONFIG_FILE` itself) accepts a leading `~` per entry, expanded to
the home directory of whichever user actually runs the command — useful for
a config file meant to work unchanged across machines or deployment users.

`serve --host`/`--port` follow the opposite rule from everything else here:
an explicit flag on the command line overrides `FLEDERMAP_HOST`/
`FLEDERMAP_PORT` (or the config file), rather than the other way around,
since a flag typed at invocation time should win over a standing default.

`poiidx_database_url` connects Fledermap to a *separate* poiidx instance
(`../poiidx` on this machine, published on PyPI as `poiidx`) used to name derived sites. It must
point at a dedicated `poiidx_bats_db` database — never `poiidx_db` (a pre-existing, unrelated
poiidx index) or `bats_db` (Fledermap's own storage). poiidx hashes its own schema and filter
config on init and **drops and recreates all its tables** on any mismatch, the same hazard the
database section above already warns about for `bats_db`. See
`docs/superpowers/specs/2026-08-28-fledermap-poiidx-site-naming-design.md` for the full design.

### Option A: environment variables only

```bash
export FLEDERMAP_DATABASE_URL="postgresql://fledermap:password@localhost/bats_db"
# archive_roots is required -- comma-separated for more than one directory,
# scanned in the order given.
export FLEDERMAP_ARCHIVE_ROOTS="/path/to/archive"
# media_root is optional (falls back to a platformdirs data dir), but a real
# deployment should still set it explicitly -- especially in a container,
# where the fallback path is inside the container's own ephemeral
# filesystem, not backed up and gone on the next `docker run`.
export FLEDERMAP_MEDIA_ROOT="/var/lib/fledermap/media"
fledermap ingest
```

### Option B: a config file

Write a TOML file at the `platformdirs` config location for your OS (on
Linux, `~/.config/fledermap/config.toml`), or point `FLEDERMAP_CONFIG_FILE`
at any file of your choosing:

```toml
# ~/.config/fledermap/config.toml
database_url = "postgresql://fledermap:password@localhost/bats_db"
# archive_roots is required -- a TOML array, even for a single directory.
# Scanned in the order listed (design spec §2/§4).
archive_roots = ["/path/to/archive"]
# media_root is optional (falls back to a platformdirs data dir, e.g.
# ~/.local/share/fledermap on Linux), but set it explicitly for any real
# deployment -- especially in a container, where the fallback path is inside
# the container's own ephemeral filesystem, not backed up and gone on the
# next `docker run`. See CLAUDE.md's note on FLEDERMAP_MEDIA_ROOT for why
# this differs from static_root just below.
media_root = "/var/lib/fledermap/media"

# Everything below is optional -- shown with its default value.
# static_root defaults to a platformdirs cache dir (e.g. ~/.cache/fledermap
# on Linux) and rarely needs setting. `~` expands to whichever user's home
# directory runs the command, so this line works for any deployment as-is:
# static_root = "~/.cache/fledermap"
# timestamp_source = "filename"       # or "metadata"
# default_timezone = "UTC"            # any IANA zone name, e.g. "Europe/Berlin"
# session_gap_hours = 6.0
# site_eps_m = 75.0
# site_min_points = 3
# transect_distance_m = 150.0
```

An unknown key in this file (a typo like `sesion_gap_hours`) is rejected at
startup rather than silently ignored. The file itself is entirely optional:
if nothing exists at the default location, that's not an error, and every
setting falls back to its env var or hardcoded default as if the file
setting didn't exist. Pointing `FLEDERMAP_CONFIG_FILE` at an exact path that
doesn't exist, though, *is* an error — naming a file explicitly is a request
for that specific file.

### Mixing both

Any setting can come from either source; env vars override the file
setting-by-setting, not file-vs-file wholesale. A common pattern is
committing most settings to a config file and overriding just the database
password via env at deploy time:

```toml
# config.toml, checked into a deploy script or left on the host
media_root = "/var/lib/fledermap/media"
site_eps_m = 50.0
```

```bash
# database_url deliberately kept out of the file, kept as a secret in the env
export FLEDERMAP_DATABASE_URL="postgresql://fledermap:password@localhost/bats_db"
fledermap serve
```

## 3. Running it

```bash
fledermap ingest     # scan every configured archive root into the database
fledermap derive     # partition sessions, cluster sites
fledermap serve      # the web map, http://127.0.0.1:5000
```

`serve` also needs vendor JS/CSS (Leaflet, HTMX, Alpine), fetched from
unpkg.com and checked against a pinned SHA-256 before anything is written.
Nothing to do here normally: `serve` fetches whatever's missing into
`static_root` automatically on startup, and does nothing over the network at
all once that cache is warm. To pre-warm it deliberately instead — ahead of
an offline/air-gapped deployment, or to force a full verified re-fetch —
run:

```bash
fledermap fetch-assets
```

See `fledermap --help` and each subcommand's own `--help` for the rest of
the CLI surface (`worker`, `enqueue-media`, `--sweep`/`--no-sweep`, etc.).
