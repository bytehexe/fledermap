from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.classify_implications import (
    antecedent_key,
    mine_classify_implications,
    subset_keys,
)
from fledermap.store.models import (
    ClassifyImplicationRule,
    Identification,
    Recording,
    Taxon,
)

pytestmark = pytest.mark.db


def test_antecedent_key_sorts_and_joins() -> None:
    assert antecedent_key([24, 1]) == "1,24"


def test_antecedent_key_single_taxon() -> None:
    assert antecedent_key([7]) == "7"


def test_subset_keys_of_one_atom_is_itself() -> None:
    assert subset_keys([5]) == ["5"]


def test_subset_keys_of_two_atoms_includes_both_singles_and_the_pair() -> None:
    keys = subset_keys([1, 2])
    assert set(keys) == {"1", "2", "1,2"}


def test_subset_keys_of_three_atoms_never_produces_a_size_three_key() -> None:
    """Antecedent size is capped at 2 (spec §1) -- every subset above size 2 must be
    excluded even when the recording's own atom set is larger."""
    keys = subset_keys([1, 2, 3])
    assert set(keys) == {"1", "2", "3", "1,2", "1,3", "2,3"}
    assert "1,2,3" not in keys


def test_subset_keys_of_empty_atoms_is_empty() -> None:
    assert subset_keys([]) == []


def _taxon(session: OrmSession, name: str) -> Taxon:
    t = Taxon(rank="species", scientific_name=name)
    session.add(t)
    session.flush()
    return t


def _recording(session: OrmSession, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def _ident(
    session: OrmSession,
    recording: Recording,
    source: IdSource,
    *,
    verdict: Verdict = Verdict.SPECIES,
    taxon: Taxon | None = None,
) -> None:
    session.add(
        Identification(
            recording_id=recording.id,
            source=source,
            verdict=verdict,
            taxon_id=taxon.id if taxon is not None else None,
        ),
    )


def test_mining_ignores_recordings_with_no_manual_claim(engine: Engine) -> None:
    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        session.commit()

        count = mine_classify_implications(
            session, min_support=1, min_confidence=0.5, min_lift=1.0
        )
        session.commit()

        assert count == 0
        assert session.scalar(select(ClassifyImplicationRule).limit(1)) is None


def test_mining_stores_a_qualifying_single_atom_rule(engine: Engine) -> None:
    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        noc = _taxon(session, "Nyctalus noctula")
        # Three recordings: automatic claims Pip -> MANUAL always confirms Pip. A fourth,
        # unrelated recording keeps Pip's base rate from being 100% (so lift is finite and
        # meaningfully > 1, not a degenerate infinite/undefined case).
        for i in range(3):
            r = _recording(session, f"{i}" * 64)
            _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
            _ident(session, r, IdSource.MANUAL, taxon=pip)
        r4 = _recording(session, "f" * 64)
        _ident(session, r4, IdSource.EMT_GUANO, taxon=noc)
        _ident(session, r4, IdSource.MANUAL, taxon=noc)
        session.commit()

        count = mine_classify_implications(
            session, min_support=3, min_confidence=0.6, min_lift=1.0
        )
        session.commit()

        assert count == 1
        rule = session.scalar(select(ClassifyImplicationRule))
        assert rule is not None
        assert rule.antecedent_key == antecedent_key([pip.id])
        assert rule.conclusion_verdict == Verdict.SPECIES
        assert rule.conclusion_taxon_id == pip.id
        assert rule.support == 3
        assert rule.confidence == 1.0


def test_mining_excludes_rules_below_min_support(engine: Engine) -> None:
    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        _ident(session, r, IdSource.MANUAL, taxon=pip)
        session.commit()

        count = mine_classify_implications(
            session, min_support=3, min_confidence=0.6, min_lift=1.0
        )
        session.commit()

        assert count == 0


def test_mining_excludes_a_base_rate_dominant_conclusion_via_min_lift(
    engine: Engine,
) -> None:
    """The scenario spec §1's min_lift gate exists to catch: species D is the MANUAL pick
    in most examples regardless of antecedent, so an unrelated antecedent {X} still clears
    min_confidence for {X}->D purely from D's prevalence. A genuinely predictive rule in the
    same dataset (antecedent {Y}, whose conclusion rate is well above D's base rate) must
    still survive.

    NOTE on the padding recording: base_rate(D) = conclusion_total[D] / example_total (spec
    §3), and every MANUAL claim -- including one on a recording with no automatic claim at
    all -- is its own training example that increments both the numerator (if it concludes
    D) and the denominator. To pad the denominator to 8 without moving D's count, the extra
    recording must therefore conclude something OTHER than D (here, a distinct taxon E) --
    concluding D itself would make D 7/8 = 0.875, not 6/8 = 0.75, and at that base rate
    {Y}->D's lift is 1.0/0.875 ~= 1.14, below min_lift=1.3, so the intended assertion (that
    {Y}->D survives) would not hold. Traced by hand against the mining formula before writing
    this test.
    """
    with OrmSession(engine) as session:
        d = _taxon(session, "Species D")
        x = _taxon(session, "Species X")
        y = _taxon(session, "Species Y")
        e = _taxon(session, "Species E")

        # 8 recordings total. D is the MANUAL pick in 6 of them (base rate 0.75) --
        # regardless of automatic antecedent. {X} co-occurs with D by pure prevalence
        # (3 of 4 {X} recordings conclude D, same as D's overall rate -- lift ~= 1).
        # NOTE: "x" + str(i) + "0"*62 == 64 chars (audio_hash is VARCHAR(64)); the brief's
        # original "0"*63 padding overflowed by one character and raised
        # StringDataRightTruncation against a real Postgres column -- caught by actually
        # running this test, not by inspection.
        for i in range(3):
            r = _recording(session, f"x{i}" + "0" * 62)
            _ident(session, r, IdSource.EMT_GUANO, taxon=x)
            _ident(session, r, IdSource.MANUAL, taxon=d)
        r_x_e = _recording(session, "x" + "3" + "0" * 62)
        _ident(session, r_x_e, IdSource.EMT_GUANO, taxon=x)
        _ident(session, r_x_e, IdSource.MANUAL, taxon=e)

        # {Y} is genuinely predictive of D: every {Y} recording concludes D, well above
        # D's 0.75 overall base rate (this alone still wouldn't prove informativeness if D
        # were dominant everywhere -- lift measures the gap against the SAME base rate).
        for i in range(3):
            r = _recording(session, f"y{i}" + "0" * 62)
            _ident(session, r, IdSource.EMT_GUANO, taxon=y)
            _ident(session, r, IdSource.MANUAL, taxon=d)

        # One more recording with no automatic claim at all, concluding a non-D species
        # (E again), to pad the denominator to 8 without touching D's numerator -- keeping
        # D's base rate at 6/8 = 0.75 rather than 6/7 (see the docstring note above).
        r_plain = _recording(session, "p" * 64)
        _ident(session, r_plain, IdSource.MANUAL, taxon=e)

        session.commit()

        mine_classify_implications(
            session, min_support=1, min_confidence=0.6, min_lift=1.3
        )
        session.commit()

        rules = session.scalars(select(ClassifyImplicationRule)).all()
        keys_to_conclusions = {(r.antecedent_key, r.conclusion_taxon_id) for r in rules}
        # {X} -> D must be excluded: confidence 0.75 clears min_confidence, but lift
        # ~= 0.75 / 0.75 = 1.0, below min_lift=1.3.
        assert (antecedent_key([x.id]), d.id) not in keys_to_conclusions
        # {Y} -> D must survive: confidence 1.0, lift = 1.0 / 0.75 = 1.33... >= 1.3.
        assert (antecedent_key([y.id]), d.id) in keys_to_conclusions


def test_mining_rebuilds_from_scratch_removing_stale_rows(engine: Engine) -> None:
    """A rule that qualified on a previous run but no longer does must be gone after a
    fresh mining run -- no incremental updates, no dirty-tracking (spec §4)."""
    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        noc = _taxon(session, "Nyctalus noctula")
        for i in range(3):
            r = _recording(session, f"{i}" * 64)
            _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
            _ident(session, r, IdSource.MANUAL, taxon=pip)
        session.commit()
        mine_classify_implications(
            session, min_support=3, min_confidence=0.6, min_lift=1.0
        )
        session.commit()
        assert session.scalar(select(ClassifyImplicationRule)) is not None

        # Wipe every Pip/MANUAL pairing and replace with disagreeing data, so the old
        # rule no longer qualifies.
        session.execute(delete(Identification))
        for i in range(3):
            r = _recording(session, f"g{i}" + "0" * 62)
            _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
            _ident(session, r, IdSource.MANUAL, taxon=noc)
        session.commit()

        mine_classify_implications(
            session, min_support=3, min_confidence=0.6, min_lift=1.0
        )
        session.commit()

        rules = session.scalars(select(ClassifyImplicationRule)).all()
        assert len(rules) == 1
        assert rules[0].conclusion_taxon_id == noc.id


def test_multi_species_manual_claim_decomposes_into_one_rule_per_conclusion(
    engine: Engine,
) -> None:
    """Spec §2: automatic atoms {X, Y}, MANUAL claims {A, B} -> two training rows, {X,Y}->A
    and {X,Y}->B -- never a single multi-conclusion rule.

    min_confidence=0.5, not the project default 0.6: spec §3 increments
    `antecedent_total[subset]` once per training EXAMPLE, and §2 decomposes one
    two-species MANUAL call into two examples sharing the same antecedent -- so here every
    {X,Y} example concludes either A or B and nothing else, which puts a hard ceiling of
    0.5 on confidence({X,Y}->A) and confidence({X,Y}->B) alike (they always sum to 1.0
    across exactly two always-co-occurring conclusions). A 0.6 default would make both
    rules unreachable by construction, not just under-supported -- traced by hand against
    the mining formula before lowering it here.
    """
    with OrmSession(engine) as session:
        x = _taxon(session, "Species X")
        y = _taxon(session, "Species Y")
        a = _taxon(session, "Species A")
        b = _taxon(session, "Species B")
        for i in range(3):
            r = _recording(session, f"{i}" * 64)
            _ident(session, r, IdSource.EMT_GUANO, taxon=x)
            _ident(session, r, IdSource.BATDETECT2, taxon=y)
            _ident(session, r, IdSource.MANUAL, taxon=a)
            _ident(session, r, IdSource.MANUAL, taxon=b)
        session.commit()

        mine_classify_implications(
            session, min_support=3, min_confidence=0.5, min_lift=1.0
        )
        session.commit()

        conclusions = {
            r.conclusion_taxon_id
            for r in session.scalars(select(ClassifyImplicationRule)).all()
            if r.antecedent_key == antecedent_key([x.id, y.id])
        }
        assert conclusions == {a.id, b.id}
