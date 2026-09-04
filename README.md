# fledermap

Map-first organiser for bat recordings from handheld detectors.

- Design: [`docs/superpowers/specs/2026-08-23-fledermap-design.md`](docs/superpowers/specs/2026-08-23-fledermap-design.md)
- Authoritative sources for species codes, names and file formats: [`docs/references.md`](docs/references.md)

## Supported species

fledermap resolves the species codes emitted by Wildlife Acoustics detectors
(Echo Meter Touch / Kaleidoscope) to a taxon with scientific, English, and
(where established) German common names. Bundled coverage, from the
[Wildlife Acoustics species list](https://answers.wildlifeacoustics.com/r/en-US/Bat-Auto-ID-Performance-and-Supported-Species/Bat-Auto-ID-Supported-Species-and-Abbreviated-Codes):

- **Europe** — all 31 species on that list ([`taxa_eu.yaml`](src/fledermap/store/data/taxa_eu.yaml))
- **North America (USA/Canada)** — all 38 species on that list ([`taxa_na.yaml`](src/fledermap/store/data/taxa_na.yaml))

A species code the detector emits but that isn't in either list resolves to no
taxon rather than a guess, and shows up in the review queue as an unmapped
species — see `docs/references.md` for why a missing mapping is preferred over
a wrong one.
