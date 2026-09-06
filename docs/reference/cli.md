# CLI commands

`fledermap install` (see [Set up Fledermap](../how-to/setup.md)) is enough
for normal operation — the commands below are for troubleshooting, one-off
maintenance, or a manual re-run outside the systemd services.

Every command needs a working [configuration](configuration.md)
(`FLEDERMAP_DATABASE_URL`/`database_url` at minimum) unless noted
otherwise.

## `fledermap ingest`

Scans every configured archive root and writes recordings to the database.
Read-only on the archive — never moves, renames, or modifies a source
recording. Runs automatically as part of `fledermap worker`; the manual
command is for a one-off re-scan (e.g. after moving the archive to a new
path).

`--sweep`/`--no-sweep` (default: `--sweep`) — whether to flag recordings
whose source file is no longer found on disk.

## `fledermap derive`

Partitions ingested recordings into sessions (by detector + time gap) and
rebuilds sites (by geographic clustering). Also runs automatically as part
of `fledermap worker`. Safe to re-run at any time.

`--force` — re-resolve site names even where a cached answer already
exists, for a site-naming configuration change whose effect would
otherwise never take effect for existing sites.

## `fledermap worker`

Runs continuously: the background job queue (media rendering, site
naming), plus a filesystem watcher that triggers `ingest`+`derive` shortly
after new files appear, with a five-minute cron backstop in case the watch
itself misses something. This is what `fledermap install` runs as
`fledermap-worker.service`.

`--wait`/`--no-wait` (default: `--wait`) — keep running until stopped, or
process whatever's currently queued once and exit.

## `fledermap serve`

Runs the web map. This is what `fledermap install` runs as
`fledermap-serve.service`.

`--host`, `--port` — override the configured interface/port for this run
only.

## `fledermap fetch-assets`

Fetches the map page's vendor JS/CSS (Leaflet, HTMX, Alpine) into the
static-asset cache. `serve` already does this automatically for whatever's
missing on startup; this command is only for pre-warming that cache
deliberately — ahead of an offline/air-gapped deployment, or to force a
full, freshly-verified re-fetch of everything. Needs no database
configuration, only the static-root setting.

## `fledermap enqueue-media`

Queues media-rendering jobs (spectrogram, oscillogram, audio preview) for
any recording missing one on disk — for recordings ingested before that
render existed, or after a rendering-parameter change invalidates old
output.

## `fledermap backfill-site-names`

Resolves a name for any site still missing one — for sites that predate
site naming, or whose naming job failed past its retry budget. A no-op if
site naming (`poiidx_database_url`) isn't configured.

`--force` — same meaning as `derive`'s `--force` above.

## `fledermap install`

Generates and enables the systemd `--user` services this project runs as.
See [Set up Fledermap](../how-to/setup.md). Linux only. Safe to re-run
(e.g. after upgrading).

`--restart` — also restart the already-running services now, so an
in-place upgrade takes effect immediately rather than only on next login.
