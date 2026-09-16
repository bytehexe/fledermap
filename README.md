# fledermap

Map-first organiser for bat recordings from handheld detectors. Point it at
the folder your detector (currently Wildlife Acoustics Echo Meter Touch)
syncs its recordings into, and it builds a browsable map of where and when
you recorded, with automatic species identification, session/site
clustering, spectrograms, and audibilised (time-expanded/heterodyne)
playback — a self-hosted web app you open in your browser, no cloud
service involved.

![Fledermap's map view with a site cluster and a recording's detail drawer open, showing its spectrogram, oscillogram, and species identification](docs/assets/map-drawer.png)

Only Wildlife Acoustics EMT recordings (GUANO/`wamd` metadata plus the
detector's own filename convention) are recognized today — files from other
detectors are silently skipped rather than causing an error. If you'd like
your device supported, please
[open an issue](https://github.com/bytehexe/fledermap/issues) with a sample
recording (subject to a license that allows us to use it as a test
fixture). Species identification natively covers Europe and North America
(USA/Canada) — see the docs for the exact species list.

## Quickstart

1. [Set up Fledermap](docs/how-to/setup.md) — write a config file naming
   your detector's export folder, then `fledermap install`.
2. Sync recordings into that folder as usual — they appear on the map on
   their own, no further action needed.

## Optional: BatDetect2 classifier

Fledermap can optionally run a second species classifier,
[BatDetect2](https://github.com/macaodha/batdetect2), alongside the
detector's own on-device identification. It's off by default and never
installed by a plain `pip install fledermap` — turning it on requires a
separate `pip install fledermap[batdetect2]` plus setting
`FLEDERMAP_BATDETECT2_ENABLED=true` (see
[Configuration](docs/reference/configuration.md)). **BatDetect2's model is
licensed CC-BY-NC-4.0, restricting it to non-commercial use**, and it
requires attribution to the
[BatDetect2 project](https://github.com/macaodha/batdetect2), whose model
this integration runs unmodified.

## Optional: BattyBirdNET-Analyzer classifier

A third, independently enable/disable-able classifier,
[BattyBirdNET-Analyzer](https://github.com/rdz-oss/BattyBirdNET-Analyzer) (by R.D. Zinck,
CC-BY-NC-SA-4.0), can also be enabled. Unlike batdetect2, Fledermap never imports, vendors, or
bundles any of its code -- it only talks HTTP to a server you run yourself (see
https://github.com/bytehexe/battybirdnet-docker for one way to run it), configured via
`FLEDERMAP_BATTYBIRDNET_URL`. Set `FLEDERMAP_BATTYBIRDNET_ENABLED=true` and
`FLEDERMAP_BATTYBIRDNET_URL` to your server's base URL to turn it on. Use of the server you point
Fledermap at is non-commercial only, per BattyBirdNET-Analyzer's own license -- that obligation
is yours as the operator running it, separate from Fledermap's own MIT license.

## Documentation

Everything past this quickstart — how-to guides, reference material, and
the reasoning behind the design — lives at
**https://bytehexe.github.io/fledermap/**.

## Built with agentic engineering

Fledermap's code, and most of this documentation, was written by an AI
agent (Claude/Claude Code) working with a human maintainer, not typed by
hand. If that matters to you as a user or a potential contributor — either
way, that's fair — this is stated upfront so you can decide for yourself.

## Security note

Fledermap currently has **no authentication on any route**, and none is
currently planned. Anyone who can reach the host — anyone on the same
network, or further if port-forwarded — has full read/write access. Treat
it accordingly, and if you need authentication,
[open an issue](https://github.com/bytehexe/fledermap/issues) so it can be
prioritized.

## For contributors

See [`docs/contributing.md`](docs/contributing.md).
