# Supported species

Fledermap resolves the species codes your detector emits to a taxon with
scientific, English, and (where established) German common names. Bundled
coverage, from the
[Wildlife Acoustics species list](https://answers.wildlifeacoustics.com/r/en-US/Bat-Auto-ID-Performance-and-Supported-Species/Bat-Auto-ID-Supported-Species-and-Abbreviated-Codes):

- **Europe** — all 31 species on that list.
- **North America (USA/Canada)** — all 38 species on that list.

A species code your detector emits but that isn't on either list resolves
to no taxon rather than a guess, and shows up on the map's Taxon filter as
**Unmapped species** — see
[The classifier model](../explanation/classifier-model.md) for why a
missing mapping is preferred over a wrong one.
