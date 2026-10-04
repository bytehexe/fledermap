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
| Noise classifier SNR threshold (dB) | `noise_snr_threshold_db` | `FLEDERMAP_NOISE_SNR_THRESHOLD_DB` | no | `2.0` |
| Site clustering radius (metres) | `site_eps_m` | `FLEDERMAP_SITE_EPS_M` | no | `75.0` |
| Site minimum points | `site_min_points` | `FLEDERMAP_SITE_MIN_POINTS` | no | `3` |
| poiidx database connection | `poiidx_database_url` | `FLEDERMAP_POIIDX_DATABASE_URL` | no | unset — site naming and the map's Region filter disabled |
| Site-naming search radius (metres) | `site_naming_radius_m` | `FLEDERMAP_SITE_NAMING_RADIUS_M` | no | `1000.0` |
| Web server's interface | `host` | `FLEDERMAP_HOST` | no | `127.0.0.1` |
| Web server's port | `port` | `FLEDERMAP_PORT` | no | `5000` |
| batdetect2 classifier enabled | `batdetect2_enabled` | `FLEDERMAP_BATDETECT2_ENABLED` | no | `false` |
| batdetect2 detection threshold | `batdetect2_threshold` | `FLEDERMAP_BATDETECT2_THRESHOLD` | no | `0.8` |
| batdetect2 annotation display threshold | `batdetect2_annotation_display_threshold` | `FLEDERMAP_BATDETECT2_ANNOTATION_DISPLAY_THRESHOLD` | no | `0.20` |
| BattyBirdNET classifier enabled | `battybirdnet_enabled` | `FLEDERMAP_BATTYBIRDNET_ENABLED` | no | `false` |
| BattyBirdNET-Analyzer server URL | `battybirdnet_url` | `FLEDERMAP_BATTYBIRDNET_URL` | only if enabled | unset |
| BattyBirdNET detection threshold | `battybirdnet_threshold` | `FLEDERMAP_BATTYBIRDNET_THRESHOLD` | no | `0.6` |
| BattyBirdNET annotation display threshold | `battybirdnet_annotation_display_threshold` | `FLEDERMAP_BATTYBIRDNET_ANNOTATION_DISPLAY_THRESHOLD` | no | `0.20` |
| Combine EMT sources in the UI | `combine_emt_display` | `FLEDERMAP_COMBINE_EMT_DISPLAY` | no | `true` |
| Mined-suggestion minimum support | `classify_implication_min_support` | `FLEDERMAP_CLASSIFY_IMPLICATION_MIN_SUPPORT` | no | `3` |
| Mined-suggestion minimum confidence | `classify_implication_min_confidence` | `FLEDERMAP_CLASSIFY_IMPLICATION_MIN_CONFIDENCE` | no | `0.6` |
| Mined-suggestion minimum lift | `classify_implication_min_lift` | `FLEDERMAP_CLASSIFY_IMPLICATION_MIN_LIFT` | no | `1.3` |
| RTC sidebar widget enabled | `rtc_enabled` | `FLEDERMAP_RTC_ENABLED` | no | `true` |
| Server latitude | `latitude` | `FLEDERMAP_LATITUDE` | no (both or neither) | unset |
| Server longitude | `longitude` | `FLEDERMAP_LONGITUDE` | no (both or neither) | unset |

Notes on individual settings:

- **`archive_roots`** accepts more than one directory, scanned in order:
  a TOML array in the config file, or a comma-separated list for the env
  var (`FLEDERMAP_ARCHIVE_ROOTS=/mnt/syncthing,/mnt/sdcard-dump`). Order
  only matters when the exact same recording turns up under two different
  roots — it's attributed to whichever root was scanned first.
- **`media_root`** — its default (a `platformdirs` data directory) is a
  real, persistent location on a normal single-host install, so most
  installs can leave it unset. Override it if you want the generated
  spectrograms/previews stored somewhere specific, or in a container
  deployment, where that same default path is ephemeral and gone on the
  next restart.
- **`static_root`**'s default (unlike `media_root`'s) is genuinely fine to
  leave as-is for any deployment — the vendor JS/CSS it holds is small and
  automatically re-fetched if missing, not something you place
  deliberately.
- **`default_timezone`** is an ingest-time fallback only, used when a recording's metadata
  carries no offset evidence at all. It is unrelated to the UI's *display* timezone: that is
  discovered automatically from the database server's own configured `timezone` setting at
  startup, is not configurable via any `FLEDERMAP_*` setting, and applies to every timestamp the
  web app renders regardless of this setting — see `docs/superpowers/specs/
  2026-09-14-fledermap-timezone-display-design.md`.
- Every path-typed setting (`archive_roots`, `media_root`, `static_root`,
  `FLEDERMAP_CONFIG_FILE` itself) accepts a leading `~`, expanded to the
  home directory of whichever user actually runs the command.
- **`host`/`port`** are the one exception to "env var always wins": an
  explicit `--host`/`--port` flag on `fledermap serve` overrides both the
  env var and the config file, since a flag typed at invocation time
  should win over a standing default.
- **`batdetect2_enabled`/`batdetect2_threshold`** turn on a second,
  optional species classifier ([BatDetect2](https://github.com/macaodha/batdetect2))
  run alongside the EMT's own on-device identification. `batdetect2_enabled`
  defaults to `false`; `batdetect2_threshold` (default `0.8`, valid range
  `0.0`–`1.0`) is how sure the model must be of the *species* before its
  guess is kept as a claim. BatDetect2 reports two scores per call — one for
  "there is a bat call here at all" and one for "and it is this species" —
  and this setting is compared against the second, so raising it discards
  uncertain species guesses rather than quiet calls. It is re-applied to the
  already-stored raw scores on every ingest cycle, so changing it takes
  effect immediately with no re-classification needed.
  **BatDetect2's model weights are licensed CC-BY-NC-4.0 — non-commercial
  use only** — which is why it's never installed by a plain
  `pip install fledermap`: you must separately run
  `pip install fledermap[batdetect2]` before setting `batdetect2_enabled =
  true`. Enabling it without that extra installed doesn't break the rest of
  the application — it fails only the classification job itself, at run
  time.
- **`batdetect2_annotation_display_threshold`** (default `0.20`, valid range `0.0`–`1.0`) gates
  whether a batdetect2 annotation box is *drawn on the recording-detail page at all* — separate
  from `batdetect2_threshold` above, which only affects whether a detection becomes a species
  claim and whether its box is drawn dashed vs. solid. Every detection above the model's own fixed
  5% storage floor is always cached, regardless of this setting; this only controls what the
  detail page renders, and can be set below `batdetect2_threshold` to show boxes the species
  threshold isn't confident enough to claim.
- **`battybirdnet_enabled`/`battybirdnet_url`/`battybirdnet_threshold`** turn on a third,
  optional species classifier ([BattyBirdNET-Analyzer](https://github.com/rdz-oss/BattyBirdNET-Analyzer)),
  reached over HTTP rather than run in-process. `battybirdnet_enabled` defaults to `false`;
  when set to `true`, `battybirdnet_url` becomes required — it must point at a
  BattyBirdNET-Analyzer server the operator runs and builds themselves (e.g.
  `http://localhost:7667`). Unlike BatDetect2, no `pip install fledermap[...]` extra exists for
  this: Fledermap never imports or vendors any BattyBirdNET code, only POSTs recordings to the
  configured server, so there is no CC-BY-NC licensing concern on Fledermap's side.
  `battybirdnet_threshold` (default `0.6`, matching the upstream tool's own suggested confidence
  bar, valid range `0.0`–`1.0`) is compared against the returned per-species confidence the same
  way `batdetect2_threshold` is, and is likewise re-applied to stored raw scores on every ingest
  cycle with no re-classification needed.
- **`battybirdnet_annotation_display_threshold`** (default `0.20`, valid range `0.0`–`1.0`) is the
  same display-only gate as `batdetect2_annotation_display_threshold` above, applied to
  BattyBirdNET's annotation boxes. Unlike batdetect2's genuinely per-call boxes, a BattyBirdNET box
  spans one or more of its fixed-width analysis windows merged together (consecutive windows the
  model gave the same winning species) and carries no frequency range — it renders as a
  full-height vertical band rather than a box confined to a call's actual frequency span.
- **`combine_emt_display`** (default `true`) folds the map's three on-device EMT auto-ID sources
  (`emt.guano`/`emt.wamd`/`emt.filename` — different materializations of the same underlying
  detector result) into a single combined "EMT" option in the Source filter dropdown and a single
  "emt" row in a recording's Identifications breakdown. The three `Identification` rows stay
  separate in the database either way; this only changes what the UI shows. `emt.manual` (a
  human's on-device correction) is never folded in. Set to `false` to see each of the three
  sources on its own.
- **`rtc_enabled`** (default `true`) enables the RTC (real time clock) sidebar widget,
  which displays sunrise/sunset and moon-phase calculations for astronomical planning. Set to
  `false` to hide the widget. Unlike `batdetect2_enabled`/`battybirdnet_enabled`, this defaults
  to `true` since it has no licensing or heavyweight-dependency concerns. Times shown in the
  widget follow the database server's configured `TimeZone` setting (the same display timezone
  mentioned above in `default_timezone`'s description).
- **`latitude`/`longitude`** (both optional, must both be set or both be unset) configure a fixed
  server-side location for RTC calculations. When set, this location is used instead of browser
  geolocation — the browser then shows no geolocation prompt and the widget always displays the
  configured location's sun/moon data. When both are unset, the browser prompts for geolocation
  once per page load (and the user can deny it to leave the widget empty). Latitude must be
  between -90 and 90; longitude must be between -180 and 180. Both are decimal degrees
  (e.g. `51.5074` for north, `-0.1278` for west).

## Example config file

```toml
# ~/.config/fledermap/config.toml
database_url = "postgresql://fledermap:password@localhost/bats_db"
archive_roots = ["/path/to/your/detector/archive"]

# Everything below is optional. media_root's actual default is a
# platformdirs data directory, not a fixed path -- shown here is a typical
# override instead.
# media_root = "/var/lib/fledermap/media"
# timestamp_source = "filename"       # or "metadata"
# default_timezone = "UTC"            # any IANA zone name, e.g. "Europe/Berlin"
# session_gap_hours = 6.0
# noise_snr_threshold_db = 2.0
# site_eps_m = 75.0
# site_min_points = 3
# classify_implication_min_support = 3
# classify_implication_min_confidence = 0.6
# classify_implication_min_lift = 1.3

# RTC sidebar widget: sunrise/sunset/moon-phase planning calculations
# rtc_enabled = true               # enable/disable the widget
# latitude = 51.5074               # optional: fixed server location (both lat/lon or neither)
# longitude = -0.1278              # optional: fixed server location (both lat/lon or neither)
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
