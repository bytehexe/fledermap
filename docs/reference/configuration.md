# Configuration

Every setting can be provided as an environment variable or as a key in a
TOML config file. **An environment variable always wins** over the config
file when both are set for the same setting.

Without a Docker deployment, most people should use a config file — write
it once, and every `fledermap` command and systemd service picks it up
without needing environment variables set anywhere. It lives at the
`platformdirs` config location for your OS (on Linux,
`~/.config/fledermap/config.toml`), or wherever `FLEDERMAP_CONFIG_FILE`
points instead. The file is entirely optional: if nothing exists at the
default location, every setting just falls back to its environment
variable or hardcoded default. Naming `FLEDERMAP_CONFIG_FILE` explicitly
and having *that* file be absent, though, is an error — naming a file is a
request for that specific file. An unknown key in the file (a typo) is
also rejected at startup rather than silently ignored.

| Setting | Config file key | Env var | Required? | Default |
|---|---|---|---|---|
| Database connection | `database_url` | `FLEDERMAP_DATABASE_URL` | **yes** | — |
| Archive root(s) to scan | `archive_roots` | `FLEDERMAP_ARCHIVE_ROOTS` | **yes** | — |
| Derived media directory | `media_root` | `FLEDERMAP_MEDIA_ROOT` | no | a `platformdirs` data dir |
| Vendor JS/CSS directory | `static_root` | `FLEDERMAP_STATIC_ROOT` | no | a `platformdirs` cache dir |
| Timestamp source | `timestamp_source` | `FLEDERMAP_TIMESTAMP_SOURCE` | no | `filename` |
| Fallback timezone | `default_timezone` | `FLEDERMAP_DEFAULT_TIMEZONE` | no | `UTC` |
| Session gap (hours) | `session_gap_hours` | `FLEDERMAP_SESSION_GAP_HOURS` | no | `6.0` |
| Site clustering radius (metres) | `site_eps_m` | `FLEDERMAP_SITE_EPS_M` | no | `75.0` |
| Site minimum points | `site_min_points` | `FLEDERMAP_SITE_MIN_POINTS` | no | `3` |
| poiidx database connection | `poiidx_database_url` | `FLEDERMAP_POIIDX_DATABASE_URL` | no | unset — site naming disabled |
| Site-naming search radius (metres) | `site_naming_radius_m` | `FLEDERMAP_SITE_NAMING_RADIUS_M` | no | `1000.0` |
| Web server's interface | `host` | `FLEDERMAP_HOST` | no | `127.0.0.1` |
| Web server's port | `port` | `FLEDERMAP_PORT` | no | `5000` |

Notes on individual settings:

- **`archive_roots`** accepts more than one directory, scanned in order:
  a TOML array in the config file, or a comma-separated list for the env
  var (`FLEDERMAP_ARCHIVE_ROOTS=/mnt/syncthing,/mnt/sdcard-dump`). Order
  only matters when the exact same recording turns up under two different
  roots — it's attributed to whichever root was scanned first.
- **`media_root`** has a default, but a real install should set it
  explicitly anyway. The fallback location is fine for trying Fledermap
  out, but is the wrong place for data you actually want to keep around —
  particularly in a container, where "the platform default data
  directory" is some ephemeral path gone on the next restart.
- **`static_root`**'s default (unlike `media_root`'s) is genuinely fine to
  leave as-is for any deployment — the vendor JS/CSS it holds is small and
  automatically re-fetched if missing, not something you place
  deliberately.
- Every path-typed setting (`archive_roots`, `media_root`, `static_root`,
  `FLEDERMAP_CONFIG_FILE` itself) accepts a leading `~`, expanded to the
  home directory of whichever user actually runs the command.
- **`host`/`port`** are the one exception to "env var always wins": an
  explicit `--host`/`--port` flag on `fledermap serve` overrides both the
  env var and the config file, since a flag typed at invocation time
  should win over a standing default.

## Example config file

```toml
# ~/.config/fledermap/config.toml
database_url = "postgresql://fledermap:password@localhost/bats_db"
archive_roots = ["/path/to/your/detector/archive"]
media_root = "/var/lib/fledermap/media"

# Everything below is optional -- shown with its default value.
# timestamp_source = "filename"       # or "metadata"
# default_timezone = "UTC"            # any IANA zone name, e.g. "Europe/Berlin"
# session_gap_hours = 6.0
# site_eps_m = 75.0
# site_min_points = 3
```

## Mixing a config file with environment variables

A common pattern: commit most settings to a config file, and keep the
database password out of it as a secret set only at deploy time.

```toml
# config.toml
media_root = "/var/lib/fledermap/media"
site_eps_m = 50.0
```

```bash
export FLEDERMAP_DATABASE_URL="postgresql://fledermap:password@localhost/bats_db"
fledermap serve
```
