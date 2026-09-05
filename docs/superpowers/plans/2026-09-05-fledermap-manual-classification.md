# Fledermap Manual Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human classify a recording from the recording-details page — one or more species/group taxa, `No ID`, `Noise`, or "no opinion" — while fixing the precedence bug that lets an automatic `NO_ID` shadow a real answer, and making every claim on a multi-species file actually findable by the taxon filter.

**Architecture:** `current_best_identification` changes from "pick one winning `Identification`" to "pick a winning *source*, surface all of that source's claims" via a new `CurrentIdentification` wrapper — every existing caller updates to match. A new `services/manual_classification.py` writes `IdSource.MANUAL` rows (nothing does today) via a supersede-then-insert function, exposed through one POST route and a tag-multiselect UI fragment. Two smaller, independent slices ride along: the verdict filter gains an "Unidentified" option distinct from "No ID", and the recording-details page gains an annotated "Identifications" breakdown box.

**Tech Stack:** Flask, SQLAlchemy, Jinja2, htmx, vanilla JS (no frontend build step), pytest + `testcontainers` (`db`-marked tests), `puppeteer-core` for live JS verification (no JS test harness exists in this repo).

**Spec:** `docs/superpowers/specs/2026-09-05-fledermap-manual-classification-design.md`

## Global Constraints

- Never hand-enter a species/group code without a verified, cited source in `docs/references.md` — every code in this plan (`MYSP`, `HiF`, `LoF`, `Hilo`, `NOTBAT`) is already verified there (2026-09-05, NABat's own species-codes page). Do not add, rename, or invent any further code.
- `NOTBAT`'s real NABat spelling is exactly `Hilo` (not `HiLo`) and `NOTBAT` (not `NOBAT`) — copy these literally, they've been wrong in an earlier draft of this spec already.
- No new `Verdict` enum member and no Alembic migration anywhere in this plan — confirmed unnecessary (spec Design §1, §2). If any task seems to need one, stop and re-check against the spec before proceeding.
- `hatch test -m "not db"` must pass after every task; `db`-marked tests (`hatch test`, unsandboxed) must pass by the end of the final task testing that area.
- `hatch run types:check` must pass after every task.
- Every JS-only change must be verified live (no JS test harness exists) using this project's established `puppeteer-core` technique — see `reference-headless-chrome-live-verification-technique` conventions: launch headless Chrome against a temp `hatch run fledermap serve` instance, `page.setCacheEnabled(false)` before navigating.
- Run `git` with `dangerouslyDisableSandbox: true` (sandboxed git config writes leave a stale `.git/config.lock`); `db`-marked tests likewise need `dangerouslyDisableSandbox: true` (Docker is blocked by the sandbox).

---

## Task 1: Region-neutral `taxa_groups.yaml` — relocate `Myotis`/`Nyctaloid`, add the new group/genus taxa

**Files:**
- Create: `src/fledermap/store/data/taxa_groups.yaml`
- Modify: `src/fledermap/store/data/taxa_eu.yaml` (remove the `Myotis`/`Nyctaloid` block and its header comment)
- Modify: `src/fledermap/store/seed.py:15` (`_DATA` list)
- Modify: `docs/references.md` (already updated with the NABat verification in a prior commit — confirm no further change needed)
- Modify: `CLAUDE.md` (Species codes section: "`seed.py`'s `_DATA` loads both files" → "all three files")
- Test: `tests/test_seed.py`

**Interfaces:**
- Consumes: nothing from other tasks (fully independent, can run first).
- Produces: `Taxon` rows queryable by `scientific_name` — `"Myotis"` (`rank="genus"`, now with a `TaxonCode(source="nabat", code="MYSP")`), `"Nyctaloid"` (`rank="group"`, unchanged, relocated only), `"Plecotus"` (`rank="genus"`, no code), `"HiF"`/`"LoF"`/`"Hilo"`/`"NOTBAT"` (`rank="group"`, each with its own `nabat` `TaxonCode`). Every later task that looks up a taxon by name relies on these existing after this task.

- [ ] **Step 1: Write the failing test for the new file being loaded**

```python
# tests/test_seed.py -- add near the top, after the existing imports
def test_taxa_groups_yaml_is_in_the_seed_data_list() -> None:
    """A future edit to _DATA that forgets this file would otherwise fail
    silently -- seed_taxonomy would just seed fewer taxa, no error raised."""
    from fledermap.store.seed import _DATA

    assert "taxa_groups.yaml" in _DATA
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch test tests/test_seed.py::test_taxa_groups_yaml_is_in_the_seed_data_list -v`
Expected: FAIL — `AssertionError: assert 'taxa_groups.yaml' in ['taxa_eu.yaml', 'taxa_na.yaml']`

- [ ] **Step 3: Create `taxa_groups.yaml`**

```yaml
# src/fledermap/store/data/taxa_groups.yaml
#
# Genus- and group-rank taxa: never a species, and (per the parent design spec's
# decision D10) deliberately region-neutral -- shared across taxa_eu.yaml/
# taxa_na.yaml rather than filed under either, because a genus can span both
# (Myotis, Eptesicus) and a group built from such genera (Nyctaloid) inherits the
# same problem. Filing them under one region's file was an earlier, pre-this-file
# mistake (Myotis/Nyctaloid originally lived in taxa_eu.yaml) that caused no live
# bug only because nothing reads file-of-origin as a region signal yet -- fixed
# here before any group/genus taxa in this project.
#
# `codes: {}` means genuinely no code exists for that taxon -- check
# docs/references.md before assuming one does and hand-entering it. MYOSPP was
# invented once already (against the Wildlife Acoustics species list) and had to
# be removed; every code below is instead verified against NABat's own published
# species-codes page (docs/references.md, 2026-09-05).
taxa:
  - scientific_name: Myotis
    rank: genus
    common_name_de: Mausohren
    common_name_en: Mouse-eared bats
    codes: {nabat: MYSP}
  - scientific_name: Nyctaloid
    rank: group
    common_name_de: Nyctaloid
    common_name_en: Nyctaloid
    codes: {}
  - scientific_name: Plecotus
    rank: genus
    common_name_de: Langohren
    common_name_en: Long-eared bats
    codes: {}
  - scientific_name: HiF
    rank: group
    common_name_en: Various species with pulses having a minimum frequency higher than ~30 kHz
    codes: {nabat: HiF}
  - scientific_name: LoF
    rank: group
    common_name_en: Various species with pulses having a minimum frequency lower than ~30 kHz
    codes: {nabat: LoF}
  - scientific_name: Hilo
    rank: group
    common_name_en: Two or more bats from distinct frequency classes vocalizing simultaneously within a recording
    codes: {nabat: Hilo}
  - scientific_name: NOTBAT
    rank: group
    common_name_en: Not a bat
    codes: {nabat: NOTBAT}
```

- [ ] **Step 4: Update `seed.py`'s `_DATA`**

In `src/fledermap/store/seed.py`, change:

```python
_DATA = ["taxa_eu.yaml", "taxa_na.yaml"]
```

to:

```python
_DATA = ["taxa_eu.yaml", "taxa_na.yaml", "taxa_groups.yaml"]
```

- [ ] **Step 5: Remove `Myotis`/`Nyctaloid` from `taxa_eu.yaml`**

Find and delete this exact block from `src/fledermap/store/data/taxa_eu.yaml` (it sits right after `Vespertilio murinus`, at the end of the species list):

```yaml
  # Genus and group taxa carry NO code: the EMT emits species codes only.
  # They exist as targets for manual identifications (spec D10 — not every
  # identification is a species).
  - scientific_name: Myotis
    rank: genus
    common_name_de: Mausohren
    common_name_en: Mouse-eared bats
    codes: {}
  - scientific_name: Nyctaloid
    rank: group
    common_name_de: Nyctaloid
    common_name_en: Nyctaloid
    codes: {}
```

The file's `taxa:` list should end with `Vespertilio murinus` after this deletion. Do not touch the "This covers all 31 European species" header line — it already only counted `rank: species` rows, so it stays accurate.

- [ ] **Step 6: Run the seed tests**

Run: `hatch test tests/test_seed.py -v` (this file is `pytest.mark.db` — run with `dangerouslyDisableSandbox: true`, real Docker required)
Expected: `test_taxa_groups_yaml_is_in_the_seed_data_list` now PASSES. `test_group_and_genus_ranks_are_representable` (pre-existing, asserts `Myotis`/genus and `Nyctaloid`/group exist by `scientific_name`) should still PASS unchanged, since it queries the DB by name, not by source file.

- [ ] **Step 7: Add tests for the new taxa and their codes**

```python
# tests/test_seed.py -- add after test_group_and_genus_ranks_are_representable
def test_new_group_and_genus_taxa_are_seeded(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        plecotus = session.scalars(
            select(Taxon).where(Taxon.scientific_name == "Plecotus"),
        ).one()
        assert plecotus.rank == "genus"

        for name in ("HiF", "LoF", "Hilo", "NOTBAT"):
            taxon = session.scalars(
                select(Taxon).where(Taxon.scientific_name == name),
            ).one()
            assert taxon.rank == "group"


def test_myotis_now_resolves_mysp(engine: Engine) -> None:
    """Myotis existed with codes: {} before this plan; MYSP is its first code."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = resolve_code(session, "nabat", "MYSP")

        assert taxon is not None
        assert taxon.scientific_name == "Myotis"


def test_plecotus_has_no_taxon_code(engine: Engine) -> None:
    """No authoritative European-equivalent code exists (checked directly against
    NABat's own page, 2026-09-05, docs/references.md) -- must not be invented."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        plecotus = session.scalars(
            select(Taxon).where(Taxon.scientific_name == "Plecotus"),
        ).one()
        code_count = session.scalar(
            select(func.count())
            .select_from(TaxonCode)
            .where(TaxonCode.taxon_id == plecotus.id),
        )
        assert code_count == 0


def test_group_codes_resolve_to_the_right_taxon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        for code, name in (("HiF", "HiF"), ("LoF", "LoF"), ("Hilo", "Hilo"), ("NOTBAT", "NOTBAT")):
            taxon = resolve_code(session, "nabat", code)
            assert taxon is not None
            assert taxon.scientific_name == name
```

- [ ] **Step 8: Run all seed tests**

Run: `hatch test tests/test_seed.py -v`
Expected: all PASS, including the four new tests and the pre-existing ones.

- [ ] **Step 9: Update `CLAUDE.md`**

In `CLAUDE.md`'s "Species codes" section, find:

```
`taxa_eu.yaml` covers all 31 European species and `taxa_na.yaml` all 38 North
American (USA/Canada) species on the Wildlife Acoustics list (2026-09-04, in
anticipation of both EU and US users at v1) — `seed.py`'s `_DATA` loads both
files.
```

Change the last clause to:

```
`taxa_groups.yaml` holds every genus/group-rank taxon (never a species),
deliberately region-neutral rather than filed under either regional file —
`Myotis`/`Nyctaloid` moved there from `taxa_eu.yaml` (2026-09-05) after both
turned out to be cross-region (Myotis has species in both lists; Nyctaloid's
Eptesicus spans both continents too), which had gone unnoticed only because
nothing read file-of-origin as a region signal. `seed.py`'s `_DATA` loads all
three files.
```

- [ ] **Step 10: Run the full non-db suite and type check**

Run: `hatch test -m "not db"` — expect PASS (no other file references `_DATA`'s length or exact contents outside `test_seed.py`).
Run: `hatch run types:check` — expect `Success: no issues found`.

- [ ] **Step 11: Commit**

```bash
git add src/fledermap/store/data/taxa_groups.yaml src/fledermap/store/data/taxa_eu.yaml src/fledermap/store/seed.py tests/test_seed.py CLAUDE.md
git commit -m "feat: region-neutral taxa_groups.yaml for genus/group taxa

Relocates Myotis/Nyctaloid out of taxa_eu.yaml (both are cross-region:
Myotis has NA species too, Nyctaloid's Eptesicus spans both continents)
into a new, region-neutral taxa_groups.yaml. Adds Plecotus, HiF, LoF,
Hilo, and NOTBAT there too, and gives Myotis its first real TaxonCode
(nabat/MYSP). All five new codes verified against NABat's own
species-codes page (docs/references.md).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `CurrentIdentification` wrapper, precedence rewrite, and migrate every caller

**Files:**
- Modify: `src/fledermap/services/current_best.py` (full rewrite of `current_best_identification`, new `CurrentIdentification` dataclass, `recording_headline` signature change)
- Modify: `src/fledermap/services/map_query.py` (`_passes_verdict_filter`, `filtered_recordings`'s taxon-filter branch, `site_detail`'s species tally)
- Modify: `src/fledermap/web/api/geojson.py` (`_recording_feature`)
- Modify: `src/fledermap/web/views/map.py` (drawer panel's `best`/`taxon` lookup)
- Modify: `src/fledermap/web/views/sessions.py` (`session_detail_page`'s `best`/`taxon` lookup)
- Modify: `src/fledermap/web/views/recording_detail.py` (`recording_details_page`'s `best`/`taxon` lookup — also gains every current taxon, needed by Task 5's classifier box)
- Modify: `src/fledermap/web/static/app.js` (`colorForFeature`, new `MULTI_SPECIES_COLOR` reserved constant)
- Modify: `tests/test_current_best.py` (every existing test updated to the new wrapper type, plus new precedence/multi-species tests)
- Modify: `tests/test_map_view.py` (taxon-filter-finds-either-claim regression, `site_detail` tally)
- Modify: `tests/test_geojson_api.py` (`multi_species` property)

**Interfaces:**
- Consumes: nothing from Task 1 (independent; both can run in either order, though Task 1 is listed first since it's smaller).
- Produces: `CurrentIdentification` (dataclass: `claims: tuple[Identification, ...]`, properties `is_multi: bool`, `primary: Identification`, `verdict: Verdict`, `taxon_ids: frozenset[int]`, classmethod `from_matches`) and `current_best_identification(recording: Recording) -> CurrentIdentification | None` — every later task (3, 4, 5) reads `best.primary`/`best.claims`/`best.taxon_ids`/`best.is_multi` on this type, never a bare `Identification` again.

- [ ] **Step 1: Write the failing tests for the new precedence rules and wrapper shape**

Replace the entire contents of `tests/test_current_best.py` with:

```python
from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import (
    CurrentIdentification,
    current_best_identification,
    recording_headline,
)
from fledermap.store.models import Identification, Recording, Taxon


def _recording(*identifications: Identification) -> Recording:
    r = Recording(
        audio_hash="a" * 64,
        path="x.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    r.identifications = list(identifications)
    return r


def _ident(
    source: IdSource,
    *,
    taxon_id: int | None = 1,
    verdict: Verdict = Verdict.SPECIES,
    superseded: bool = False,
    first_seen_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
) -> Identification:
    return Identification(
        source=source,
        verdict=verdict,
        taxon_id=taxon_id,
        first_seen_at=first_seen_at,
        superseded_at=datetime(2026, 8, 26, tzinfo=UTC) if superseded else None,
    )


def test_manual_wins_over_every_other_source() -> None:
    r = _recording(
        _ident(IdSource.EMT_GUANO),
        _ident(IdSource.MANUAL, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.MANUAL
    assert best.primary.taxon_id == 2


def test_emt_guano_beats_emt_wamd_beats_emt_filename() -> None:
    r = _recording(
        _ident(IdSource.EMT_FILENAME, taxon_id=1),
        _ident(IdSource.EMT_WAMD, taxon_id=2),
        _ident(IdSource.EMT_GUANO, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 3


def test_superseded_identifications_are_ignored() -> None:
    r = _recording(
        _ident(IdSource.MANUAL, superseded=True),
        _ident(IdSource.EMT_GUANO, taxon_id=5),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 5


def test_no_identifications_returns_none() -> None:
    r = _recording()

    assert current_best_identification(r) is None


def test_all_superseded_returns_none() -> None:
    r = _recording(_ident(IdSource.EMT_GUANO, superseded=True))

    assert current_best_identification(r) is None


def test_two_non_superseded_claims_from_the_same_source_break_on_recency() -> None:
    r = _recording(
        _ident(
            IdSource.EMT_GUANO,
            taxon_id=1,
            first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _ident(
            IdSource.EMT_GUANO,
            taxon_id=2,
            first_seen_at=datetime(2026, 6, 1, tzinfo=UTC),
        ),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.taxon_id == 2


def test_automatic_no_id_is_passive_and_a_lower_precedence_species_wins() -> None:
    """The ^a549ce fix: a high-precedence automatic NO_ID no longer shadows a
    real SPECIES answer further down the precedence order."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.EMT_WAMD, taxon_id=7),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_WAMD
    assert best.primary.taxon_id == 7


def test_automatic_no_id_with_nothing_below_it_returns_none() -> None:
    """Every automatic source drew a blank -- current_best_identification
    returns None (displayed as "unidentified"), not a NO_ID result."""
    r = _recording(_ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None))

    assert current_best_identification(r) is None


def test_manual_no_id_is_active_and_shadows_a_lower_precedence_species() -> None:
    """Unlike an automatic NO_ID, a MANUAL NO_ID is a deliberate human judgment
    and shadows everything below it -- it does not fall through."""
    r = _recording(
        _ident(IdSource.MANUAL, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.EMT_GUANO, taxon_id=9),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.MANUAL
    assert best.verdict == Verdict.NO_ID


def test_noise_is_always_active_even_for_automatic_sources() -> None:
    r = _recording(
        _ident(IdSource.EMT_GUANO, verdict=Verdict.NOISE, taxon_id=None),
        _ident(IdSource.EMT_WAMD, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.verdict == Verdict.NOISE


def test_multiple_manual_species_claims_all_surface() -> None:
    r = _recording(
        _ident(IdSource.MANUAL, taxon_id=1),
        _ident(IdSource.MANUAL, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})


def test_single_claim_is_not_multi() -> None:
    r = _recording(_ident(IdSource.EMT_GUANO, taxon_id=1))

    best = current_best_identification(r)

    assert best is not None
    assert not best.is_multi
    assert best.taxon_ids == frozenset({1})


def test_primary_of_multiple_manual_claims_is_the_first_added() -> None:
    r = _recording(
        _ident(
            IdSource.MANUAL,
            taxon_id=1,
            first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _ident(
            IdSource.MANUAL,
            taxon_id=2,
            first_seen_at=datetime(2026, 6, 1, tzinfo=UTC),
        ),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.taxon_id == 1


def test_current_identification_from_matches_wraps_a_list() -> None:
    ident = _ident(IdSource.EMT_GUANO, taxon_id=1)

    wrapped = CurrentIdentification.from_matches([ident])

    assert wrapped.claims == (ident,)
    assert wrapped.primary is ident


def test_recording_headline_prefers_the_resolved_taxon() -> None:
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    best = CurrentIdentification.from_matches([_ident(IdSource.EMT_GUANO, taxon_id=1)])

    assert recording_headline(taxon, best) == "Pipistrellus pipistrellus"


def test_recording_headline_is_unidentified_with_no_best() -> None:
    assert recording_headline(None, None) == "unidentified"


def test_recording_headline_shows_the_raw_code_for_an_unmapped_species() -> None:
    ident = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)
    ident.raw_label = "EPTNIL"
    best = CurrentIdentification.from_matches([ident])

    assert recording_headline(None, best) == "EPTNIL (unmapped species)"


def test_recording_headline_falls_back_to_the_verdict_value_with_no_raw_label() -> None:
    ident = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)
    best = CurrentIdentification.from_matches([ident])

    assert recording_headline(None, best) == "species"


def test_recording_headline_shows_no_id_and_noise_verdicts_directly() -> None:
    no_id = CurrentIdentification.from_matches(
        [_ident(IdSource.MANUAL, taxon_id=None, verdict=Verdict.NO_ID)],
    )
    noise = CurrentIdentification.from_matches(
        [_ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.NOISE)],
    )

    assert recording_headline(None, no_id) == "no_id"
    assert recording_headline(None, noise) == "noise"


def test_recording_headline_shows_multiple_species_for_a_multi_claim_result() -> None:
    best = CurrentIdentification.from_matches(
        [
            _ident(IdSource.MANUAL, taxon_id=1),
            _ident(IdSource.MANUAL, taxon_id=2),
        ],
    )

    assert recording_headline(None, best) == "Multiple Species"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_current_best.py -v`
Expected: FAIL — `ImportError: cannot import name 'CurrentIdentification'`

- [ ] **Step 3: Rewrite `services/current_best.py`**

Replace the entire file with:

```python
"""'Current best' identification -- design spec P4-2, resolving the parent
spec's (section 5) explicit but never-implemented rule: "manual wins, else
highest-priority non-superseded source by configured order." Not a stored
column -- recomputed on every call, so the order below can change without a
migration.

Rewritten 2026-09-05 (docs/superpowers/specs/2026-09-05-fledermap-manual-
classification-design.md) to fix a real precedence bug and support
multi-species files: a source's claims are only skipped (treated as absent)
when they are PURELY automatic NO_ID -- an automatic classifier's admission
of uncertainty must not shadow a real answer further down the precedence
order. NOISE is always active, and MANUAL is always active including its own
NO_ID, since a human's judgment is deliberate rather than an admission of
uncertainty. A winning MANUAL source may carry more than one non-superseded
SPECIES/group claim (a genuine multi-species file); CurrentIdentification
represents that instead of forcing a single winner."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Taxon

_PRECEDENCE: tuple[IdSource, ...] = (
    IdSource.MANUAL,
    IdSource.EMT_MANUAL,
    IdSource.EMT_GUANO,
    IdSource.EMT_WAMD,
    IdSource.EMT_FILENAME,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CurrentIdentification:
    """One or more non-superseded claims from the single winning source
    (§2/§3 of the design spec). `claims` has more than one entry only when
    the winning source is MANUAL with multiple SPECIES/group claims -- every
    other source is resolved to exactly one claim, same as before this
    rewrite."""

    claims: tuple[Identification, ...]

    @property
    def is_multi(self) -> bool:
        return len(self.claims) > 1

    @property
    def primary(self) -> Identification:
        """The single representative claim for headline/marker-color
        purposes -- first-added (lowest first_seen_at), matching how ties
        within one source already broke before this rewrite."""
        return min(self.claims, key=lambda i: i.first_seen_at or _EPOCH)

    @property
    def verdict(self) -> Verdict:
        return self.primary.verdict

    @property
    def taxon_ids(self) -> frozenset[int]:
        return frozenset(c.taxon_id for c in self.claims if c.taxon_id is not None)

    @classmethod
    def from_matches(cls, matches: Sequence[Identification]) -> CurrentIdentification:
        return cls(claims=tuple(matches))


def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Walk sources in precedence order. A source's claims are skipped
    (fall through to the next source) only when they are ALL automatic
    NO_ID -- any SPECIES, NOISE, or MANUAL claim of any verdict wins
    outright and stops the walk."""
    candidates = [i for i in recording.identifications if i.superseded_at is None]
    for source in _PRECEDENCE:
        matches = [i for i in candidates if i.source == source]
        if not matches:
            continue
        if source != IdSource.MANUAL and all(
            m.verdict == Verdict.NO_ID for m in matches
        ):
            continue
        return CurrentIdentification.from_matches(matches)
    return None


def recording_headline(taxon: Taxon | None, best: CurrentIdentification | None) -> str:
    """The species/verdict label every recording headline renders
    (recording_details.html, _recording_panel.html, session_detail.html each
    had their own copy of the same ternary -- centralized here so a fix only
    has to happen once).

    - A resolved `taxon` always wins: its scientific name (the single-claim,
      single-taxon case -- callers resolve `taxon` from `best.primary.taxon_id`
      today, unaffected by multi-species results since a multi-species
      caller passes `taxon=None` and lets the branch below handle it).
    - `best.is_multi`: "Multiple Species" -- individual claims are only
      enumerated in the classifier box (spec §5), not the headline.
    - No identification at all: "unidentified".
    - `NO_ID`/`NOISE` verdicts show their own value (`best.verdict.value`).
    - A real `SPECIES` verdict whose code never mapped to a `Taxon`: shown as
      "<code> (unmapped species)" using `best.primary.raw_label`.
    """
    if taxon is not None:
        return taxon.scientific_name
    if best is None:
        return "unidentified"
    if best.is_multi:
        return "Multiple Species"
    if best.verdict == Verdict.SPECIES and best.primary.raw_label:
        return f"{best.primary.raw_label} (unmapped species)"
    return best.verdict.value
```

- [ ] **Step 4: Run `test_current_best.py` alone**

Run: `hatch test tests/test_current_best.py -v`
Expected: all PASS.

- [ ] **Step 5: Migrate `map_query.py`**

In `src/fledermap/services/map_query.py`:

Change the import (no change needed — `current_best_identification` import stays the same name).

Change `_passes_verdict_filter`'s type hint and the type of `best` it receives — no code change needed inside the function body itself (it already only reads `best.verdict`, which `CurrentIdentification` also exposes), but update its type hint:

```python
def _passes_verdict_filter(
    best: CurrentIdentification | None,
    verdict: Verdict | Literal["all"] | None,
) -> bool:
```

Add the import: `from fledermap.services.current_best import CurrentIdentification, current_best_identification`

In `filtered_recordings`, change the taxon-filter branch from:

```python
            if taxon_id == "unmapped":
                matches = (
                    best is not None
                    and best.taxon_id is None
                    and best.verdict == Verdict.SPECIES
                )
            else:
                matches = best is not None and best.taxon_id == taxon_id
```

to:

```python
            if taxon_id == "unmapped":
                matches = (
                    best is not None
                    and not best.taxon_ids
                    and best.verdict == Verdict.SPECIES
                )
            else:
                matches = best is not None and taxon_id in best.taxon_ids
```

In `site_detail`, change:

```python
    for recording in recordings:
        best = current_best_identification(recording)
        if best is not None and best.taxon_id is not None:
            counts[best.taxon_id] = counts.get(best.taxon_id, 0) + 1
```

to:

```python
    for recording in recordings:
        best = current_best_identification(recording)
        if best is not None:
            for taxon_id in best.taxon_ids:
                counts[taxon_id] = counts.get(taxon_id, 0) + 1
```

- [ ] **Step 6: Migrate `geojson.py`**

In `src/fledermap/web/api/geojson.py`, change `_recording_feature`:

```python
def _recording_feature(recording: Recording, session: OrmSession) -> dict[str, object]:
    point = decode_point(recording.geom)
    best = current_best_identification(recording)
    taxon_name = None
    if best is not None and best.primary.taxon_id is not None:
        taxon = session.get(Taxon, best.primary.taxon_id)
        if taxon is not None:
            taxon_name = taxon.scientific_name
    return {
        "type": "Feature",
        "geometry": (
            {"type": "Point", "coordinates": [point[0], point[1]]}
            if point is not None
            else None
        ),
        "properties": {
            "audio_hash": recording.audio_hash,
            "recorded_at": recording.recorded_at.isoformat(),
            "taxon_id": best.primary.taxon_id if best is not None else None,
            "taxon_name": taxon_name,
            "verdict": best.verdict.value if best is not None else None,
            "source": best.primary.source.value if best is not None else None,
            "multi_species": best.is_multi if best is not None else False,
        },
    }
```

- [ ] **Step 7: Migrate `web/views/map.py`**

Change:

```python
        best = current_best_identification(recording)
        taxon = None
        if best is not None and best.taxon_id is not None:
            taxon = session.get(Taxon, best.taxon_id)
```

to:

```python
        best = current_best_identification(recording)
        taxon = None
        if best is not None and not best.is_multi and best.primary.taxon_id is not None:
            taxon = session.get(Taxon, best.primary.taxon_id)
```

(The `not best.is_multi` guard matches `recording_headline`'s contract: passing a resolved `taxon` for a multi-species result would incorrectly short-circuit the "Multiple Species" branch — a multi-species drawer panel should show "Multiple Species", not one arbitrary taxon's name.)

- [ ] **Step 8: Migrate `web/views/sessions.py`**

Apply the identical change as Step 7 to `session_detail_page`'s `best`/`taxon` block:

```python
        recordings_with_id = []
        for recording in detail.recordings:
            best = current_best_identification(recording)
            taxon = None
            if best is not None and not best.is_multi and best.primary.taxon_id is not None:
                taxon = session.get(Taxon, best.primary.taxon_id)
            recordings_with_id.append((recording, best, taxon))
```

- [ ] **Step 9: Migrate `web/views/recording_detail.py`**

Change:

```python
        best = current_best_identification(recording)
        taxon = None
        if best is not None and best.taxon_id is not None:
            taxon = session.get(Taxon, best.taxon_id)
```

to:

```python
        best = current_best_identification(recording)
        taxon = None
        if best is not None and not best.is_multi and best.primary.taxon_id is not None:
            taxon = session.get(Taxon, best.primary.taxon_id)
        current_taxa: list[Taxon] = []
        if best is not None and best.taxon_ids:
            current_taxa = list(
                session.scalars(
                    select(Taxon).where(Taxon.id.in_(best.taxon_ids)),
                ).all(),
            )
```

Add `current_taxa=current_taxa` to the `flask.render_template(...)` call's keyword arguments (used by Task 5's classifier box to pre-populate the tag editor with the recording's current manual claims — unused by the template until then, but the plumbing belongs here where `best`/`session` are already in scope).

- [ ] **Step 10: Add the `MULTI_SPECIES_COLOR` constant and update `colorForFeature` in `app.js`**

In `src/fledermap/web/static/app.js`, near the other reserved-color constants (after `const GPS_TRACK_COLOR = "#00bcd4";`), add:

```javascript
// Multi-species recordings (current_best_identification returning more than
// one claim -- a real multi-species file, or several manual group/species
// tags on one recording) get their own fixed, non-hash-derived color so they
// can never collide with a real taxon's generated hue.
const MULTI_SPECIES_COLOR = "#ff9800";
```

Update the reserved-colors comment block above it to add a line:
```
//   MULTI_SPECIES_COLOR -- multi_species: true (see below)
```

Change `colorForFeature`:

```javascript
function colorForFeature(props) {
  if (props.multi_species) return MULTI_SPECIES_COLOR;
  if (props.verdict === "noise") return "gray";
  if (props.verdict === "no_id") return "orange";
  if (props.taxon_id !== null && props.taxon_id !== undefined) {
    return colorForTaxon(props.taxon_id);
  }
  return "#333333";
}
```

- [ ] **Step 11: Update `tests/test_geojson_api.py` for `multi_species`**

Add, following the existing test conventions in that file (real DB, `Identification` rows added directly to a `Recording`):

```python
def test_recordings_geojson_marks_multi_species_recordings(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        taxon_b = Taxon(rank="genus", scientific_name="Myotis")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        recording = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        recording.identifications = [
            Identification(
                source=IdSource.MANUAL,
                verdict=Verdict.SPECIES,
                taxon_id=taxon_a.id,
            ),
            Identification(
                source=IdSource.MANUAL,
                verdict=Verdict.SPECIES,
                taxon_id=taxon_b.id,
            ),
        ]
        session.add(recording)
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson?verdict=all")

    feature = response.get_json()["features"][0]
    assert feature["properties"]["multi_species"] is True


def test_recordings_geojson_single_species_is_not_multi(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="d" * 64,
            path="d.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        recording.identifications = [
            Identification(
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
        ]
        session.add(recording)
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson?verdict=all")

    feature = response.get_json()["features"][0]
    assert feature["properties"]["multi_species"] is False
```

- [ ] **Step 12: Add `tests/test_map_view.py` regression for the taxon-filter fix**

Add, following that file's existing conventions (check the top of the file for its `_client`/fixture helper names and match them exactly before writing this):

```python
def test_taxon_filter_finds_either_of_two_manual_species_claims(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The actual bug fix: before this plan, only ONE arbitrarily-picked claim
    was checked against the taxon filter, so a genuine multi-species file was
    findable by only one of its two species."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        taxon_b = Taxon(rank="genus", scientific_name="Myotis")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        recording = Recording(
            audio_hash="e" * 64,
            path="e.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        recording.identifications = [
            Identification(source=IdSource.MANUAL, verdict=Verdict.SPECIES, taxon_id=taxon_a.id),
            Identification(source=IdSource.MANUAL, verdict=Verdict.SPECIES, taxon_id=taxon_b.id),
        ]
        session.add(recording)
        session.commit()
        taxon_a_id, taxon_b_id = taxon_a.id, taxon_b.id

    with OrmSession(engine) as session:
        found_a = filtered_recordings(session, taxon_id=taxon_a_id, verdict="all")
        found_b = filtered_recordings(session, taxon_id=taxon_b_id, verdict="all")

    assert len(found_a) == 1
    assert len(found_b) == 1
```

(Add the necessary imports — `filtered_recordings`, `WKTElement`, `Identification`, `IdSource`, `Verdict` — matching whichever of these the file doesn't already import at the top.)

- [ ] **Step 13: Run the full non-db and db suites**

Run: `hatch test -m "not db"` — expect PASS.
Run: `hatch test` (all, `dangerouslyDisableSandbox: true`) — expect PASS, including the new tests above.
Run: `hatch run types:check` — expect `Success: no issues found`.

- [ ] **Step 14: Live-verify the JS color change**

Using this project's established `puppeteer-core` technique: seed a throwaway DB with one multi-species recording (two `MANUAL` `SPECIES` claims on one `Recording`, matching Step 11's fixture), start a temp `hatch run fledermap serve` instance pointed at it, and screenshot the map — confirm the marker renders in `#ff9800` (orange, `MULTI_SPECIES_COLOR`), not a taxon-hash color. Tear down the throwaway DB/server afterward.

- [ ] **Step 15: Commit**

```bash
git add src/fledermap/services/current_best.py src/fledermap/services/map_query.py src/fledermap/web/api/geojson.py src/fledermap/web/views/map.py src/fledermap/web/views/sessions.py src/fledermap/web/views/recording_detail.py src/fledermap/web/static/app.js tests/test_current_best.py tests/test_geojson_api.py tests/test_map_view.py
git commit -m "fix: CurrentIdentification wrapper -- passive automatic NO_ID, multi-species support

current_best_identification now returns a CurrentIdentification wrapper
(one or more claims from a single winning source) instead of a single
Identification. Fixes ^a549ce (an automatic-source NO_ID no longer
shadows a real answer further down the precedence order; NOISE and
MANUAL stay always-active) and the real multi-species filter bug (the
taxon filter now checks every current claim, not one arbitrary pick).
Every caller migrated; multi-species recordings get a dedicated marker
color and a \"Multiple Species\" headline.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Verdict filter — "Unidentified" distinct from "No ID"

**Files:**
- Modify: `src/fledermap/web/params.py` (`parse_verdict`)
- Modify: `src/fledermap/services/map_query.py` (`_passes_verdict_filter`)
- Modify: `src/fledermap/web/templates/map.html` (verdict `<select>`)
- Modify: `docs/superpowers/specs/2026-08-25-fledermap-phase4-map-design.md` (already amended in a prior commit — confirm no further change needed)
- Test: `tests/test_params.py`, `tests/test_map_view.py`

**Interfaces:**
- Consumes: `CurrentIdentification` from Task 2 (`_passes_verdict_filter`'s `best` parameter).
- Produces: `parse_verdict(raw: str | None) -> Verdict | Literal["all", "unidentified"] | None`, consumed by `web/api/geojson.py` and `web/views/map.py` unchanged (they already just pass whatever `parse_verdict` returns straight to `filtered_recordings`/`_passes_verdict_filter` — no further caller change needed this task).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_params.py -- add near the existing parse_verdict tests
def test_parse_verdict_accepts_unidentified() -> None:
    assert parse_verdict("unidentified") == "unidentified"
```

```python
# tests/test_map_view.py -- add near the other _passes_verdict_filter-adjacent tests
def test_unidentified_filter_matches_only_no_best(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        no_best = Recording(
            audio_hash="f" * 64,
            path="f.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        manual_no_id = Recording(
            audio_hash="g" * 64,
            path="g.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        manual_no_id.identifications = [
            Identification(source=IdSource.MANUAL, verdict=Verdict.NO_ID),
        ]
        session.add_all([no_best, manual_no_id])
        session.commit()

    with OrmSession(engine) as session:
        unidentified = filtered_recordings(session, verdict="unidentified")
        no_id = filtered_recordings(session, verdict=Verdict.NO_ID)

    assert {r.audio_hash for r in unidentified} == {"f" * 64}
    assert {r.audio_hash for r in no_id} == {"g" * 64}
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_params.py::test_parse_verdict_accepts_unidentified -v`
Expected: FAIL — `ValueError` (raw `Verdict("unidentified")` raises, since it's not a real enum member).

- [ ] **Step 3: Update `parse_verdict`**

In `src/fledermap/web/params.py`, change:

```python
def parse_verdict(raw: str | None) -> Verdict | Literal["all"] | None:
    if raw is None:
        return None
    if raw == "all":
        return "all"
    return Verdict(raw)
```

to:

```python
def parse_verdict(raw: str | None) -> Verdict | Literal["all", "unidentified"] | None:
    """`unidentified` matches a recording with no current identification at
    all -- distinct from `no_id`, which (since the precedence rewrite in
    docs/superpowers/specs/2026-09-05-fledermap-manual-classification-design.md)
    can only ever come from a genuine MANUAL claim. Amends decision P4-9
    (2026-08-25-fledermap-phase4-map-design.md), which folded the two
    together before that distinction was possible."""
    if raw is None:
        return None
    if raw in ("all", "unidentified"):
        return raw
    return Verdict(raw)
```

- [ ] **Step 4: Update `_passes_verdict_filter`**

In `src/fledermap/services/map_query.py`, change:

```python
def _passes_verdict_filter(
    best: CurrentIdentification | None,
    verdict: Verdict | Literal["all"] | None,
) -> bool:
    """A recording with no non-superseded identification at all (`best is
    None`) is treated as equivalent to `Verdict.NO_ID` for this purpose --
    both mean "we don't know what this is," which is exactly what "hide noise
    by default" is protecting the map from (decision P4-9)."""
    if verdict == "all":
        return True
    effective = best.verdict if best is not None else Verdict.NO_ID
    if verdict is None:
        return effective == Verdict.SPECIES
    return effective == verdict
```

to:

```python
def _passes_verdict_filter(
    best: CurrentIdentification | None,
    verdict: Verdict | Literal["all", "unidentified"] | None,
) -> bool:
    """`unidentified` matches `best is None` exactly -- a distinct state from
    `Verdict.NO_ID`, which can now only come from a genuine MANUAL claim
    (amends decision P4-9, see parse_verdict's docstring). The default view
    (verdict=None, SPECIES-only) is unaffected either way: both `None` and
    an explicit NO_ID/NOISE result are excluded from it, exactly as before."""
    if verdict == "all":
        return True
    if verdict == "unidentified":
        return best is None
    if verdict is None:
        return best is not None and best.verdict == Verdict.SPECIES
    return best is not None and best.verdict == verdict
```

- [ ] **Step 5: Update `map.html`'s verdict `<select>`**

In `src/fledermap/web/templates/map.html`, change:

```html
        <select name="verdict" x-model="verdict">
          <option value="">Species only (default)</option>
          <option value="noise">Noise</option>
          <option value="no_id">No ID</option>
          <option value="all">All</option>
        </select>
```

to:

```html
        <select name="verdict" x-model="verdict">
          <option value="">Species only (default)</option>
          <option value="noise">Noise</option>
          <option value="no_id">No ID</option>
          <option value="unidentified">Unidentified</option>
          <option value="all">All</option>
        </select>
```

- [ ] **Step 6: Run the new and existing tests**

Run: `hatch test tests/test_params.py tests/test_map_view.py -v` (the latter is `db`-marked — `dangerouslyDisableSandbox: true`)
Expected: all PASS.

- [ ] **Step 7: Run the full suites**

Run: `hatch test -m "not db"` — expect PASS.
Run: `hatch test` — expect PASS.
Run: `hatch run types:check` — expect `Success: no issues found`.

- [ ] **Step 8: Live-verify the map filter dropdown**

Via `puppeteer-core` against a temp `hatch run fledermap serve` instance: load the map, open the Verdict `<select>`, confirm "Unidentified" appears as a distinct option between "No ID" and "All", and that selecting it actually changes the rendered markers (compare against a recording seeded with only an automatic `NO_ID` claim vs. one seeded with a `MANUAL` `NO_ID` claim).

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/params.py src/fledermap/services/map_query.py src/fledermap/web/templates/map.html tests/test_params.py tests/test_map_view.py
git commit -m "feat: distinguish \"Unidentified\" from \"No ID\" in the verdict filter

Since an automatic-only NO_ID claim is now always passive (Task 2),
Verdict.NO_ID can only come from a genuine MANUAL claim -- making \"no
identification survives\" and \"a human confirmed nothing
identifiable\" distinguishable for the first time. Adds a separate
Unidentified filter option rather than continuing to fold them
together (amends decision P4-9); the default species-only view is
unaffected.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `identification_status` helper + Identifications box on the details page

**Files:**
- Modify: `src/fledermap/services/current_best.py` (new `identification_status` function)
- Create: `src/fledermap/web/templates/_identifications_box.html`
- Modify: `src/fledermap/web/views/recording_detail.py` (compute per-identification statuses, pass to template)
- Modify: `src/fledermap/web/templates/recording_details.html` (include the new box)
- Modify: `src/fledermap/web/static/app.css` (new status classes)
- Test: `tests/test_current_best.py`, `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `CurrentIdentification` from Task 2.
- Produces: `identification_status(ident: Identification, best: CurrentIdentification | None) -> Literal["current", "passive", "shadowed", "superseded"]`, consumed only by `recording_detail.py`/`_identifications_box.html` this task, but written generically enough that Task 5's classifier box could reuse it later if needed (it won't, per the spec's placement decision — this is future-proofing, not a real Task 5 dependency).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_current_best.py -- add after the existing tests
from fledermap.services.current_best import identification_status


def test_identification_status_current_for_the_winning_claim() -> None:
    ident = _ident(IdSource.EMT_GUANO, taxon_id=1)
    r = _recording(ident)
    best = current_best_identification(r)

    assert identification_status(ident, best) == "current"


def test_identification_status_passive_for_skipped_automatic_no_id() -> None:
    passive = _ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None)
    winner = _ident(IdSource.EMT_WAMD, taxon_id=1)
    r = _recording(passive, winner)
    best = current_best_identification(r)

    assert identification_status(passive, best) == "passive"
    assert identification_status(winner, best) == "current"


def test_identification_status_passive_even_when_nothing_else_wins() -> None:
    """A passive automatic NO_ID is "passive" regardless of the overall
    outcome -- describes the row's own status, not the final result."""
    only = _ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None)
    r = _recording(only)
    best = current_best_identification(r)  # None

    assert identification_status(only, best) == "passive"


def test_identification_status_shadowed_for_a_lower_precedence_real_claim() -> None:
    winner = _ident(IdSource.MANUAL, taxon_id=1)
    loser = _ident(IdSource.EMT_GUANO, taxon_id=2)
    r = _recording(winner, loser)
    best = current_best_identification(r)

    assert identification_status(loser, best) == "shadowed"


def test_identification_status_superseded_regardless_of_precedence() -> None:
    superseded = _ident(IdSource.MANUAL, taxon_id=1, superseded=True)
    winner = _ident(IdSource.EMT_GUANO, taxon_id=2)
    r = _recording(superseded, winner)
    best = current_best_identification(r)

    assert identification_status(superseded, best) == "superseded"


def test_identification_status_every_claim_of_a_multi_species_winner_is_current() -> None:
    a = _ident(IdSource.MANUAL, taxon_id=1)
    b = _ident(IdSource.MANUAL, taxon_id=2)
    r = _recording(a, b)
    best = current_best_identification(r)

    assert identification_status(a, best) == "current"
    assert identification_status(b, best) == "current"
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_current_best.py -k identification_status -v`
Expected: FAIL — `ImportError: cannot import name 'identification_status'`

- [ ] **Step 3: Add `identification_status` to `services/current_best.py`**

Add near the bottom of the file, after `recording_headline`:

```python
from typing import Literal

IdentificationStatus = Literal["current", "passive", "shadowed", "superseded"]


def identification_status(
    ident: Identification,
    best: CurrentIdentification | None,
) -> IdentificationStatus:
    """Where one raw Identification row stands relative to the resolved
    precedence result -- for the "Identifications" breakdown box (spec §5a),
    so a human making a manual call can see *why* the page shows what it
    shows, not just the final headline in isolation.

    A row is in exactly one of these four states:
    - "superseded": a past claim from the same source, replaced by a newer
      one -- orthogonal to the other three, since current_best_identification
      never even considers superseded rows.
    - "current": one of `best.claims` -- actually driving the shown result.
    - "passive": an automatic-source NO_ID claim skipped per the precedence
      walk (services/current_best.py's `current_best_identification`) --
      true regardless of what ultimately won, since this describes the
      row's own status, not the overall outcome.
    - "shadowed": a real claim that lost only because a higher-precedence
      source's claim won outright.
    """
    if ident.superseded_at is not None:
        return "superseded"
    if best is not None and ident in best.claims:
        return "current"
    if ident.source != IdSource.MANUAL and ident.verdict == Verdict.NO_ID:
        return "passive"
    return "shadowed"
```

Add `from typing import Literal` to the top-of-file imports instead of inline if the file's existing import style groups stdlib imports together (match whatever convention `services/map_query.py` uses, which already imports `Literal` from `typing` at the top).

- [ ] **Step 4: Run `test_current_best.py`**

Run: `hatch test tests/test_current_best.py -v`
Expected: all PASS.

- [ ] **Step 5: Add CSS for the new states**

In `src/fledermap/web/static/app.css`, near the existing `.superseded` rule, add:

```css
.identification-current { font-weight: 600; }
.identification-passive, .identification-shadowed {
  color: var(--color-muted);
  font-style: italic;
}
```

- [ ] **Step 6: Create `_identifications_box.html`**

```html
{# src/fledermap/web/templates/_identifications_box.html -- the recording-details page's
   own copy of the drawer panel's "Identifications" breakdown (_recording_panel.html),
   annotated with each row's precedence status (services/current_best.py's
   identification_status) so a human making a manual classification call can see WHY the
   page shows what it shows, not just the resolved headline in isolation. Deliberately a
   separate template rather than a shared include with the drawer's version -- the drawer's
   own box is untouched by this feature (spec: manual classification is details-page-only). #}
<div class="col identifications">
  <h3>Identifications</h3>
  <ul>
    {% for ident, status in identifications_with_status %}
    <li class="identification-{{ status }}">
      {{ ident.source.value }}: {{ ident.raw_label or ident.verdict.value }}
      {% if status == "passive" %}<span>— passive, ignored</span>{% endif %}
      {% if status == "shadowed" and best %}<span>— shadowed by {{ best.primary.source.value }}</span>{% endif %}
    </li>
    {% endfor %}
  </ul>
</div>
```

- [ ] **Step 7: Wire it up in `recording_detail.py`**

In `src/fledermap/web/views/recording_detail.py`, after `best = current_best_identification(recording)` (from Task 2's Step 9), add:

```python
        identifications_with_status = [
            (ident, identification_status(ident, best))
            for ident in recording.identifications
        ]
```

Add `identification_status` to the existing `from fledermap.services.current_best import ...` line, and add `identifications_with_status=identifications_with_status` to the `flask.render_template(...)` call.

- [ ] **Step 8: Include the box in `recording_details.html`**

In `src/fledermap/web/templates/recording_details.html`, add right after the existing `<p class="detail-meta">...</p>` block (before the `detail-toolbar` div):

```html
{% include "_identifications_box.html" %}
```

- [ ] **Step 9: Add a rendering test**

```python
# tests/test_recording_detail_view.py -- add near the other page-content tests
def test_identifications_box_renders_on_the_details_page(engine, tmp_path) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="h" * 64,
            path="h.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        recording.identifications = [
            Identification(
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NO_ID,
            ),
            Identification(
                source=IdSource.EMT_WAMD,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
        ]
        session.add(recording)
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get(f"/recordings/{'h' * 64}")
    html = response.get_data(as_text=True)

    assert "Identifications" in html
    assert "identification-passive" in html
    assert "identification-current" in html
    assert "passive, ignored" in html
```

(Match whichever imports/helper names — `_app_client` or similar — this test file's existing tests already use; adjust the call signature if it differs from the guess above.)

- [ ] **Step 10: Run the tests**

Run: `hatch test tests/test_recording_detail_view.py -v` (`dangerouslyDisableSandbox: true`, `db`-marked)
Expected: PASS.

- [ ] **Step 11: Run the full suites**

Run: `hatch test -m "not db"` and `hatch test` — expect PASS.
Run: `hatch run types:check` — expect `Success: no issues found`.

- [ ] **Step 12: Live-verify the rendered box**

Via `puppeteer-core`: load a recording-details page for a recording with a mix of statuses (a passive automatic `NO_ID`, a current claim, a shadowed real claim), screenshot it, confirm the bold/muted/italic styling reads clearly and the "— passive, ignored" / "— shadowed by ..." annotations appear correctly.

- [ ] **Step 13: Commit**

```bash
git add src/fledermap/services/current_best.py src/fledermap/web/templates/_identifications_box.html src/fledermap/web/views/recording_detail.py src/fledermap/web/templates/recording_details.html src/fledermap/web/static/app.css tests/test_current_best.py tests/test_recording_detail_view.py
git commit -m "feat: annotate the Identifications box with precedence status

Brings the raw per-source \"Identifications\" breakdown to the
recording-details page for the first time (it existed only in the
drawer panel before), and annotates each row as current / passive
(automatic NO_ID, skipped per the precedence rewrite) / shadowed by a
higher-precedence source / superseded -- so a human making a manual
classification call can see why the page shows what it shows.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Manual classification — service, route, and classifier box UI

**Files:**
- Create: `src/fledermap/services/manual_classification.py`
- Create: `tests/test_manual_classification.py`
- Modify: `src/fledermap/web/views/map.py` (new POST route, matching `toggle_favourite`'s existing home for recording-mutating actions)
- Create: `src/fledermap/web/templates/_classifier_box.html`
- Modify: `src/fledermap/web/views/recording_detail.py` (build the search index + current-state data for the classifier box)
- Modify: `src/fledermap/web/templates/recording_details.html` (include the classifier box)
- Modify: `src/fledermap/web/static/app.css` (classifier box styling)
- Test: `tests/test_recording_detail_view.py` (route + rendering)

**Interfaces:**
- Consumes: `CurrentIdentification`/`current_taxa` from Task 2's `recording_detail.py` changes; `Taxon`/`TaxonCode` from Task 1's seed data (used by the search index, though not schema-dependent on it — any seeded taxon works).
- Produces: `set_manual_classification(session: OrmSession, recording: Recording, *, verdict: Verdict | None, taxon_ids: Sequence[int] = ()) -> None`, and `POST /recordings/<audio_hash>/manual-classification` — nothing later in this plan depends on either (this is the final task).

- [ ] **Step 1: Write the failing tests for `set_manual_classification`**

```python
# tests/test_manual_classification.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.manual_classification import set_manual_classification
from fledermap.store.models import Identification, Recording, Taxon

pytestmark = pytest.mark.db


def _recording(session: OrmSession, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.commit()
    return r


def _manual_claims(session: OrmSession, recording_id: int) -> list[Identification]:
    return list(
        session.scalars(
            select(Identification).where(
                Identification.recording_id == recording_id,
                Identification.source == IdSource.MANUAL,
                Identification.superseded_at.is_(None),
            ),
        ).all(),
    )


def test_species_verdict_requires_at_least_one_taxon_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "a" * 64)

        with pytest.raises(ValueError, match="taxon_id"):
            set_manual_classification(session, recording, verdict=Verdict.SPECIES, taxon_ids=[])


def test_non_species_verdict_rejects_taxon_ids(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "b" * 64)

        with pytest.raises(ValueError, match="taxon_ids"):
            set_manual_classification(
                session, recording, verdict=Verdict.NO_ID, taxon_ids=[1],
            )


def test_setting_a_species_claim_creates_a_manual_identification(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "c" * 64)

        set_manual_classification(
            session, recording, verdict=Verdict.SPECIES, taxon_ids=[taxon.id],
        )

        claims = _manual_claims(session, recording.id)
        assert len(claims) == 1
        assert claims[0].taxon_id == taxon.id
        assert claims[0].verdict == Verdict.SPECIES


def test_setting_multiple_species_claims_inserts_one_row_each(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        taxon_b = Taxon(rank="genus", scientific_name="Myotis")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        recording = _recording(session, "d" * 64)

        set_manual_classification(
            session, recording, verdict=Verdict.SPECIES, taxon_ids=[taxon_a.id, taxon_b.id],
        )

        claims = _manual_claims(session, recording.id)
        assert {c.taxon_id for c in claims} == {taxon_a.id, taxon_b.id}


def test_setting_no_id_supersedes_a_prior_species_claim(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "e" * 64)
        set_manual_classification(
            session, recording, verdict=Verdict.SPECIES, taxon_ids=[taxon.id],
        )

        set_manual_classification(session, recording, verdict=Verdict.NO_ID)

        claims = _manual_claims(session, recording.id)
        assert len(claims) == 1
        assert claims[0].verdict == Verdict.NO_ID


def test_setting_a_species_claim_supersedes_a_prior_no_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "f" * 64)
        set_manual_classification(session, recording, verdict=Verdict.NO_ID)

        set_manual_classification(
            session, recording, verdict=Verdict.SPECIES, taxon_ids=[taxon.id],
        )

        claims = _manual_claims(session, recording.id)
        assert len(claims) == 1
        assert claims[0].verdict == Verdict.SPECIES


def test_clearing_supersedes_every_standing_manual_claim(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "g" * 64)
        set_manual_classification(
            session, recording, verdict=Verdict.SPECIES, taxon_ids=[taxon.id],
        )

        set_manual_classification(session, recording, verdict=None)

        assert _manual_claims(session, recording.id) == []


def test_clearing_when_nothing_is_set_is_a_safe_no_op(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "h" * 64)

        set_manual_classification(session, recording, verdict=None)

        assert _manual_claims(session, recording.id) == []


def test_two_manual_species_rows_insert_cleanly_under_the_unique_constraint(
    engine: Engine,
) -> None:
    """Regression: uq_identification_source_claim is (recording_id, source,
    source_version, raw_label) -- taxon_id isn't part of it, and
    source_version/raw_label are both NULL for every manual row, so two
    manual SPECIES rows differing only in taxon_id must not collide under
    postgresql_nulls_not_distinct=True."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        taxon_b = Taxon(rank="genus", scientific_name="Myotis")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        recording = _recording(session, "i" * 64)

        set_manual_classification(
            session, recording, verdict=Verdict.SPECIES, taxon_ids=[taxon_a.id, taxon_b.id],
        )
        session.commit()  # would raise IntegrityError if the constraint collided

        assert len(_manual_claims(session, recording.id)) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_manual_classification.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.services.manual_classification'`

- [ ] **Step 3: Write `services/manual_classification.py`**

```python
"""Writes IdSource.MANUAL identification rows from the recording-details
page's classifier box (design spec 2026-09-05-fledermap-manual-classification-
design.md, §4). The only place in the codebase that writes IdSource.MANUAL --
every other source is written by services/ingest.py's commit_scan during a
scan, never here."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording


def set_manual_classification(
    session: OrmSession,
    recording: Recording,
    *,
    verdict: Verdict | None,
    taxon_ids: Sequence[int] = (),
) -> None:
    """Always supersedes every standing MANUAL claim first, then inserts
    exactly what the new state calls for -- the classifier box always
    submits its full current state rather than a diff, and this function is
    the safe way to apply that (matching services/ingest.py's
    _apply_identifications' own key-based supersede-then-insert shape).

    `verdict=None` means "clear to no manual opinion" -- a real, explicit
    input, not an implicit consequence of SPECIES with an empty taxon_ids
    (that combination raises instead, so a client bug sending an empty list
    by accident fails loudly rather than silently clearing a classification).
    """
    if verdict == Verdict.SPECIES and not taxon_ids:
        msg = "verdict=SPECIES requires at least one taxon_id"
        raise ValueError(msg)
    if verdict != Verdict.SPECIES and taxon_ids:
        msg = "taxon_ids is only meaningful for verdict=SPECIES"
        raise ValueError(msg)

    now = datetime.now(UTC)
    existing = [
        i
        for i in recording.identifications
        if i.source == IdSource.MANUAL and i.superseded_at is None
    ]
    for ident in existing:
        ident.superseded_at = now

    if verdict in (Verdict.NO_ID, Verdict.NOISE):
        recording.identifications.append(
            Identification(source=IdSource.MANUAL, verdict=verdict, first_seen_at=now),
        )
    elif verdict == Verdict.SPECIES:
        for taxon_id in taxon_ids:
            recording.identifications.append(
                Identification(
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_id,
                    first_seen_at=now,
                ),
            )
    # verdict is None: "clear" -- existing MANUAL rows already superseded
    # above, nothing new inserted.
    session.commit()
```

- [ ] **Step 4: Run the service tests**

Run: `hatch test tests/test_manual_classification.py -v` (`dangerouslyDisableSandbox: true`)
Expected: all PASS.

- [ ] **Step 5: Add the route to `web/views/map.py`**

Add, right after `toggle_favourite`:

```python
@views_bp.post("/recordings/<audio_hash>/manual-classification")
def post_manual_classification(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            return flask.make_response(("Recording not found.", 404))

        verdict_raw = flask.request.form.get("verdict")
        verdict = Verdict(verdict_raw) if verdict_raw else None
        taxon_ids = [
            int(v) for v in flask.request.form.getlist("taxon_ids") if v
        ]

        try:
            set_manual_classification(
                session, recording, verdict=verdict, taxon_ids=taxon_ids,
            )
        except ValueError as exc:
            return flask.make_response((str(exc), 400))

        best = current_best_identification(recording)
        taxon = None
        if best is not None and not best.is_multi and best.primary.taxon_id is not None:
            taxon = session.get(Taxon, best.primary.taxon_id)
        identifications_with_status = [
            (ident, identification_status(ident, best))
            for ident in recording.identifications
        ]
        current_taxa = []
        if best is not None and best.taxon_ids:
            current_taxa = list(
                session.scalars(
                    select(Taxon).where(Taxon.id.in_(best.taxon_ids)),
                ).all(),
            )

        html = flask.render_template(
            "_classifier_box.html",
            recording=recording,
            best=best,
            current_taxa=current_taxa,
            taxon_search_index=_taxon_search_index(session),
        )
        response = flask.make_response(html)

    return response
```

Add `Verdict` to the existing `from fledermap.domain.codes import IdSource` line (→ `from fledermap.domain.codes import IdSource, Verdict`), add `from fledermap.services.current_best import current_best_identification, identification_status` (extend the existing import if `current_best_identification` is already imported there — check the top of `map.py` first), and `from fledermap.services.manual_classification import set_manual_classification`.

Note `_taxon_search_index` is defined in `recording_detail.py` in Step 7 below — import it: `from fledermap.web.views.recording_detail import _taxon_search_index`. (If this creates a circular import between `map.py` and `recording_detail.py`, move `_taxon_search_index` into a shared module instead — e.g. a new `web/views/_taxon_search.py` — and import it from both. Check for the circular-import error when running the tests in Step 10 before assuming either approach works.)

- [ ] **Step 6: Create `_classifier_box.html`**

```html
{# src/fledermap/web/templates/_classifier_box.html -- recording-details page only
   (design spec §5: manual classification is a deliberate, close-review action, not a
   quick map-browsing one -- not duplicated in the drawer panel). Placed below the meta
   line and above the tool toolbar (spec §5's placement decision). A tag multiselect for
   species/group taxa (backed by taxon_search_index, a small inline JSON search index --
   ~70 taxa, no per-keystroke endpoint needed at this scale) plus No ID/Noise/Clear
   controls, mutually exclusive with the tag chips per the design's within-MANUAL
   exclusivity rule -- enforced here for immediate UI feedback, but the real guarantee is
   server-side in services/manual_classification.py's set_manual_classification, which
   raises on an inconsistent combination rather than silently coercing it. #}
<div
  id="classifier-box"
  class="classifier-box"
  data-audio-hash="{{ recording.audio_hash }}"
  data-taxon-search-index="{{ taxon_search_index | tojson }}"
>
  <h3>Classify</h3>
  <div class="classifier-tags" id="classifier-tags">
    {% for taxon in current_taxa %}
    <span class="classifier-chip" data-taxon-id="{{ taxon.id }}">
      {{ taxon.scientific_name }}
      <button type="button" class="classifier-chip-remove" aria-label="Remove {{ taxon.scientific_name }}">×</button>
    </span>
    {% endfor %}
  </div>
  <input
    type="text"
    id="classifier-search"
    class="classifier-search"
    placeholder="Add a species or group…"
    autocomplete="off"
    {% if best and best.verdict != none and best.verdict.value in ("no_id", "noise") %}disabled{% endif %}
  >
  <ul id="classifier-suggestions" class="classifier-suggestions" hidden></ul>
  <div class="classifier-actions">
    <button
      type="button"
      class="tool-button classifier-verdict-button"
      data-verdict="no_id"
      aria-pressed="{{ 'true' if best and not best.is_multi and best.verdict.value == 'no_id' and best.primary.source.value == 'manual' else 'false' }}"
    >No ID</button>
    <button
      type="button"
      class="tool-button classifier-verdict-button"
      data-verdict="noise"
      aria-pressed="{{ 'true' if best and not best.is_multi and best.verdict.value == 'noise' and best.primary.source.value == 'manual' else 'false' }}"
    >Noise</button>
    <button type="button" class="tool-button" id="classifier-clear">Clear</button>
  </div>
</div>
```

- [ ] **Step 7: Build the search index and current-state data in `recording_detail.py`**

Add near the top of `src/fledermap/web/views/recording_detail.py` (module level, after the imports):

```python
def _taxon_search_index(session: OrmSession) -> list[dict[str, object]]:
    """One entry per Taxon, searched client-side against scientific_name,
    both common names, and every mapped TaxonCode.code -- small enough
    (~70 taxa) to inline as JSON rather than a per-keystroke endpoint,
    matching this project's existing "no frontend build step" scale
    assumption (design spec §5)."""
    taxa = session.scalars(select(Taxon)).all()
    codes_by_taxon: dict[int, list[str]] = {}
    for code in session.scalars(select(TaxonCode)).all():
        codes_by_taxon.setdefault(code.taxon_id, []).append(code.code)
    return [
        {
            "id": t.id,
            "scientific_name": t.scientific_name,
            "common_name_en": t.common_name_en,
            "common_name_de": t.common_name_de,
            "codes": codes_by_taxon.get(t.id, []),
        }
        for t in taxa
    ]
```

Add `TaxonCode` to the existing `from fledermap.store.models import Recording, Site, Taxon` line.

In `recording_details_page`, after the `current_taxa` block from Task 2's Step 9, add:

```python
        taxon_search_index = _taxon_search_index(session)
```

and add `taxon_search_index=taxon_search_index` to the `flask.render_template(...)` call.

- [ ] **Step 8: Include the classifier box in `recording_details.html`**

Right after the `{% include "_identifications_box.html" %}` line added in Task 4's Step 8:

```html
{% include "_classifier_box.html" %}
```

- [ ] **Step 9: Write the tag-editor JavaScript**

Create `src/fledermap/web/static/classifier_box.js`:

```javascript
// src/fledermap/web/static/classifier_box.js -- the recording-details page's manual
// classification tag editor (design spec 2026-09-05-fledermap-manual-classification-
// design.md, §5). No frontend build step in this project -- vanilla JS, matching
// audio_controls.js/recording_detail.js's own style.
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("classifier-box");
  if (!box) return;

  const searchIndex = JSON.parse(box.dataset.taxonSearchIndex);
  const audioHash = box.dataset.audioHash;
  const tagsEl = document.getElementById("classifier-tags");
  const searchEl = document.getElementById("classifier-search");
  const suggestionsEl = document.getElementById("classifier-suggestions");
  const clearButton = document.getElementById("classifier-clear");

  function currentTaxonIds() {
    return Array.from(tagsEl.querySelectorAll(".classifier-chip")).map((el) =>
      el.dataset.taxonId,
    );
  }

  function matchesQuery(taxon, query) {
    const q = query.toLowerCase();
    const fields = [
      taxon.scientific_name,
      taxon.common_name_en,
      taxon.common_name_de,
      ...(taxon.codes || []),
    ];
    return fields.some((f) => f && f.toLowerCase().includes(q));
  }

  function renderSuggestions(query) {
    suggestionsEl.innerHTML = "";
    if (!query) {
      suggestionsEl.hidden = true;
      return;
    }
    const already = new Set(currentTaxonIds().map(String));
    const matches = searchIndex
      .filter((t) => !already.has(String(t.id)) && matchesQuery(t, query))
      .slice(0, 10);
    if (matches.length === 0) {
      suggestionsEl.hidden = true;
      return;
    }
    for (const taxon of matches) {
      const li = document.createElement("li");
      li.textContent = taxon.scientific_name;
      li.dataset.taxonId = taxon.id;
      li.addEventListener("click", () => {
        addChip(taxon);
        searchEl.value = "";
        suggestionsEl.hidden = true;
      });
      suggestionsEl.appendChild(li);
    }
    suggestionsEl.hidden = false;
  }

  function addChip(taxon) {
    // Adding a species/group chip always means "classify as species" --
    // clears any active No ID/Noise selection first (mutually exclusive,
    // per the design's within-MANUAL exclusivity rule; the server enforces
    // this for real, this is just immediate UI feedback).
    document
      .querySelectorAll(".classifier-verdict-button[aria-pressed='true']")
      .forEach((btn) => btn.setAttribute("aria-pressed", "false"));
    searchEl.disabled = false;

    const chip = document.createElement("span");
    chip.className = "classifier-chip";
    chip.dataset.taxonId = taxon.id;
    chip.textContent = taxon.scientific_name + " ";
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "classifier-chip-remove";
    removeButton.setAttribute("aria-label", "Remove " + taxon.scientific_name);
    removeButton.textContent = "×";
    removeButton.addEventListener("click", () => {
      chip.remove();
      save();
    });
    chip.appendChild(removeButton);
    tagsEl.appendChild(chip);
    save();
  }

  function save() {
    const body = new URLSearchParams();
    const activeVerdictButton = document.querySelector(
      ".classifier-verdict-button[aria-pressed='true']",
    );
    if (activeVerdictButton) {
      body.append("verdict", activeVerdictButton.dataset.verdict);
    } else if (currentTaxonIds().length > 0) {
      body.append("verdict", "species");
      for (const id of currentTaxonIds()) body.append("taxon_ids", id);
    }
    // Neither branch: "clear" -- verdict omitted entirely, matching
    // set_manual_classification's verdict=None contract.

    fetch(`/recordings/${audioHash}/manual-classification`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    })
      .then((response) => response.text())
      .then((html) => {
        box.outerHTML = html;
      });
  }

  searchEl.addEventListener("input", () => renderSuggestions(searchEl.value));

  document.querySelectorAll(".classifier-verdict-button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const alreadyActive = btn.getAttribute("aria-pressed") === "true";
      document
        .querySelectorAll(".classifier-verdict-button")
        .forEach((b) => b.setAttribute("aria-pressed", "false"));
      if (!alreadyActive) {
        btn.setAttribute("aria-pressed", "true");
        tagsEl.innerHTML = "";
        searchEl.disabled = true;
      } else {
        searchEl.disabled = false;
      }
      save();
    });
  });

  clearButton.addEventListener("click", () => {
    tagsEl.innerHTML = "";
    document
      .querySelectorAll(".classifier-verdict-button[aria-pressed='true']")
      .forEach((btn) => btn.setAttribute("aria-pressed", "false"));
    searchEl.disabled = false;
    save();
  });
});
```

Note: `box.outerHTML = html` re-runs `DOMContentLoaded` listeners? No — it doesn't; replacing `outerHTML` after the initial page load does NOT re-fire `DOMContentLoaded` or re-attach these listeners to the new DOM nodes. Fix this before considering the step done: wrap the whole listener-attaching body in a named `initClassifierBox()` function, call it once on `DOMContentLoaded`, and call it again after every successful `save()` response instead of directly setting `outerHTML` blind — i.e., replace the `.then((html) => { box.outerHTML = html; })` line with:

```javascript
      .then((html) => {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = html;
        const newBox = wrapper.firstElementChild;
        box.replaceWith(newBox);
        initClassifierBox(newBox);
      });
```

and restructure the file so `initClassifierBox(box)` takes the box element as a parameter, queries `box.querySelector(...)` instead of `document.getElementById(...)` for everything scoped inside it, and is called once at the bottom on `DOMContentLoaded` with the original box, and again from within itself after each save with the freshly-swapped-in element. Rewrite the file with this structure before moving to the next step — do not ship the initial draft above as-is, it silently stops working after the first save.

- [ ] **Step 10: Include the new script and add classifier box CSS**

In `recording_details.html`, add `<script src="{{ url_for('static', filename='classifier_box.js') }}"></script>` alongside the other `<script>` tags at the bottom of the page.

In `app.css`, add:

```css
.classifier-box {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.6rem 0.75rem;
  margin: 0.75rem 0;
}
.classifier-box h3 {
  margin: 0 0 0.4rem;
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--color-muted);
}
.classifier-tags { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.4rem; }
.classifier-chip {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: 0.85rem;
}
.classifier-chip-remove {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-muted);
  font-size: 0.9rem;
  padding: 0 0.15rem;
}
.classifier-search {
  width: 100%;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
}
.classifier-suggestions {
  list-style: none;
  margin: 0.2rem 0 0;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  max-height: 12rem;
  overflow-y: auto;
}
.classifier-suggestions li { padding: 0.3rem 0.5rem; cursor: pointer; }
.classifier-suggestions li:hover { background: var(--color-bg-subtle); }
.classifier-actions { display: flex; gap: 0.4rem; margin-top: 0.5rem; }
```

- [ ] **Step 11: Add route tests**

```python
# tests/test_recording_detail_view.py -- add near the favourite-toggle tests
def test_manual_classification_route_sets_a_species_claim(engine, tmp_path) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="j" * 64,
            path="j.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()
        taxon_id = taxon.id

    client = _app_client(engine, tmp_path)
    response = client.post(
        f"/recordings/{'j' * 64}/manual-classification",
        data={"verdict": "species", "taxon_ids": [str(taxon_id)]},
    )

    assert response.status_code == 200
    assert b"Pipistrellus pipistrellus" in response.data


def test_manual_classification_route_rejects_inconsistent_input(engine, tmp_path) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="k" * 64,
            path="k.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.post(
        f"/recordings/{'k' * 64}/manual-classification",
        data={"verdict": "no_id", "taxon_ids": ["1"]},
    )

    assert response.status_code == 400


def test_manual_classification_route_404s_for_unknown_recording(engine, tmp_path) -> None:
    client = _app_client(engine, tmp_path)
    response = client.post(
        f"/recordings/{'z' * 64}/manual-classification",
        data={"verdict": "no_id"},
    )

    assert response.status_code == 404
```

(Match whichever `_app_client`-equivalent helper this test file already uses — confirmed present from Task 4's Step 9 addition to the same file.)

- [ ] **Step 12: Run all the new and existing tests**

Run: `hatch test tests/test_manual_classification.py tests/test_recording_detail_view.py -v`
Expected: all PASS.

- [ ] **Step 13: Run the full suites**

Run: `hatch test -m "not db"` — expect PASS.
Run: `hatch test` — expect PASS.
Run: `hatch run types:check` — expect `Success: no issues found`.

- [ ] **Step 14: Live-verify the classifier box end-to-end**

Via `puppeteer-core` against a temp `hatch run fledermap serve` instance with a seeded recording:
1. Load the recording-details page, confirm the classifier box renders below the meta line.
2. Type into the search field, confirm suggestions appear and filter as expected across scientific name, both common names, and codes (e.g. typing "MYSP" should suggest "Myotis").
3. Click a suggestion, confirm a chip appears and the page reflects a saved change (reload and confirm it persisted).
4. Add a second chip, confirm the recording now shows "Multiple Species" in its headline (reload the page to check) and the marker (on the map, in another tab) uses the multi-species color.
5. Click "No ID", confirm the chips clear and the button shows pressed.
6. Add a chip again, confirm "No ID" un-presses (mutual exclusion working).
7. Click "Clear", confirm everything resets and the automatic identification (if any) shows through again in the Identifications box (Task 4) as "current" rather than "shadowed".
8. Confirm the second save (step 4 above) actually re-attached working listeners on the swapped-in box — i.e. that removing a chip via its × button on the *second* save's result still works, not just the first.

- [ ] **Step 15: Commit**

```bash
git add src/fledermap/services/manual_classification.py tests/test_manual_classification.py src/fledermap/web/views/map.py src/fledermap/web/templates/_classifier_box.html src/fledermap/web/views/recording_detail.py src/fledermap/web/templates/recording_details.html src/fledermap/web/static/classifier_box.js src/fledermap/web/static/app.css tests/test_recording_detail_view.py
git commit -m "feat: manual classification from the recording-details page

The v1 backlog's biggest remaining gap: a human can now classify a
recording as one or more species/group taxa, No ID, Noise, or clear
back to no manual opinion, via a tag-multiselect box on the
recording-details page. set_manual_classification always
supersedes-then-inserts the full current state rather than diffing
client-side; the route rejects an inconsistent verdict/taxon_ids
combination with a 400 rather than silently coercing it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Final verification (after all five tasks)

- [ ] Run `hatch fmt` and confirm no changes are made (or apply and re-verify tests still pass if it reformats anything).
- [ ] Run `hatch test` (full suite, `dangerouslyDisableSandbox: true`) — expect PASS.
- [ ] Run `hatch run types:check` — expect `Success: no issues found`.
- [ ] Run `hatch build -t wheel` and `python3 -m zipfile -l dist/*.whl` — confirm `taxa_groups.yaml`, `classifier_box.js`, `_classifier_box.html`, and `_identifications_box.html` all appear in the built wheel (this project has been bitten before by a file that worked in a dev checkout but silently didn't ship).
- [ ] Re-read the full spec (`docs/superpowers/specs/2026-09-05-fledermap-manual-classification-design.md`) against the five tasks above and confirm every Decision (MC-1 through MC-9, MC-1a) has a corresponding implemented task — if any is missing, that's a plan gap to fix before calling this done, not something to implement ad hoc.
