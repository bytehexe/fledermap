from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.ingest import (
    IncompleteScanError,
    MassDisappearanceError,
    sweep_missing,
)
from fledermap.store.models import Recording

pytestmark = pytest.mark.db


def _add(session: OrmSession, count: int) -> list[str]:
    hashes = [f"{i:064d}" for i in range(count)]
    for i, digest in enumerate(hashes):
        session.add(
            Recording(
                audio_hash=digest,
                path=f"n/{i}.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ),
        )
    session.commit()
    return hashes


def test_absent_file_is_flagged_not_deleted(engine: Engine) -> None:
    """Deleting the row would destroy manually entered identifications."""
    with OrmSession(engine) as session:
        hashes = _add(session, 20)

        flagged = sweep_missing(session, set(hashes[1:]))
        session.commit()

        assert flagged == 1
        rows = session.scalars(select(Recording)).all()
        assert len(rows) == 20
        assert sum(1 for r in rows if r.missing_since is not None) == 1


def test_reappearing_file_clears_the_flag(engine: Engine) -> None:
    with OrmSession(engine) as session:
        hashes = _add(session, 20)
        sweep_missing(session, set(hashes[1:]))
        session.commit()

        sweep_missing(session, set(hashes))
        session.commit()

        assert all(r.missing_since is None for r in session.scalars(select(Recording)))


def test_mass_disappearance_is_refused(engine: Engine) -> None:
    """An unmounted archive must not flag the whole dataset."""
    with OrmSession(engine) as session:
        _add(session, 20)

        with pytest.raises(MassDisappearanceError) as excinfo:
            sweep_missing(session, set())

        assert excinfo.value.missing == 20
        assert all(r.missing_since is None for r in session.scalars(select(Recording)))


def test_threshold_boundary_is_allowed(engine: Engine) -> None:
    """Exactly 10% of 20 rows is 2 — at the threshold, not over it."""
    with OrmSession(engine) as session:
        hashes = _add(session, 20)

        assert sweep_missing(session, set(hashes[2:]), threshold=0.10) == 2


def test_empty_database_does_not_raise(engine: Engine) -> None:
    with OrmSession(engine) as session:
        assert sweep_missing(session, set()) == 0


# --- Defect 1: the ratio guard must key on newly-absent rows only ----------
#
# Without the fix, `absent` (and hence the guard) counts every row whose hash
# is missing from `seen_hashes`, including rows an earlier sweep already
# flagged. Once permanently-missing rows exceed the threshold fraction, the
# guard fires forever, even for a single newly-missing file.


def test_guard_ignores_rows_already_flagged_missing(engine: Engine) -> None:
    """Cumulative permanently-missing rows must not re-trip the guard.

    Each of the three sweeps below only newly loses one file (5% of 20, under
    the 10% threshold), so none should raise. But by the third sweep the
    CUMULATIVE absent count is 3 of 20 (15%, over threshold) — proving the
    guard keys on what's newly absent this run, not the running total.
    """
    with OrmSession(engine) as session:
        hashes = _add(session, 20)

        sweep_missing(session, set(hashes[1:]))  # row 0 newly missing (5%)
        session.commit()

        flagged_2 = sweep_missing(session, set(hashes[2:]))  # row 1 newly missing (5%)
        session.commit()
        assert flagged_2 == 1

        # Cumulative absent (rows 0, 1, 2) is 15% of 20 — over threshold —
        # but only row 2 is newly absent this run (5%), so this must not raise.
        flagged_3 = sweep_missing(session, set(hashes[3:]))
        session.commit()

        assert flagged_3 == 1
        missing_rows = [
            r for r in session.scalars(select(Recording)) if r.missing_since is not None
        ]
        assert len(missing_rows) == 3


# --- Defect 2: two-stage guard — a floor below which the ratio can't apply -


def test_small_archive_below_floor_flags_all_missing_without_raising(
    engine: Engine,
) -> None:
    """Losing 1 of 2 recordings is not 'mass disappearance' — it's normal.

    Below `min_known_for_guard` (derived from threshold), the ratio check
    cannot distinguish a single missing file from a mass event, so it must not
    fire at all; the sweep proceeds and flags whatever is newly absent.
    """
    with OrmSession(engine) as session:
        _add(session, 2)

        flagged = sweep_missing(session, set())
        session.commit()

        assert flagged == 2
        assert all(
            r.missing_since is not None for r in session.scalars(select(Recording))
        )


def test_ratio_guard_still_fires_at_the_floor(engine: Engine) -> None:
    """At or above `min_known_for_guard`, the ratio check from Defect 1 applies."""
    with OrmSession(engine) as session:
        # threshold=0.10 -> min_known_for_guard = ceil(1/0.10) = 10.
        _add(session, 10)

        with pytest.raises(MassDisappearanceError) as excinfo:
            sweep_missing(session, set())

        assert excinfo.value.missing == 10
        assert all(r.missing_since is None for r in session.scalars(select(Recording)))


def test_single_loss_never_fires_at_the_floor(engine: Engine) -> None:
    """`min_known_for_guard` must use `ceil(1/threshold)`, not `round`.

    At threshold=0.19, `round(1/0.19)` = `round(5.263)` = 5, so with 5 known
    rows `round`-based code treats the ratio guard as active — and a single
    loss out of 5 (1 > 5*0.19=0.95) exceeds it, reproducing the exact "one
    file in a small archive" defect the two-stage guard exists to prevent
    (controller-found, before this task's review). `ceil(5.263)` = 6 puts 5
    known rows BELOW the floor instead, where the guard does not apply at
    all: a single loss out of 5 must be flagged, not refused.
    """
    with OrmSession(engine) as session:
        hashes = _add(session, 5)

        flagged = sweep_missing(session, set(hashes[1:]), threshold=0.19)

        assert flagged == 1
        rows = session.scalars(select(Recording)).all()
        assert sum(1 for r in rows if r.missing_since is not None) == 1


# --- Defect 3: a caller's incomplete `seen_hashes` must not be trusted -----


def test_skipped_zero_behaves_exactly_as_before(engine: Engine) -> None:
    with OrmSession(engine) as session:
        hashes = _add(session, 20)

        flagged = sweep_missing(session, set(hashes[1:]), skipped=0)
        session.commit()

        assert flagged == 1


def test_skipped_files_refuse_to_sweep(engine: Engine) -> None:
    """A settling or transiently-unreadable file must not look like a deletion."""
    with OrmSession(engine) as session:
        _add(session, 20)

        with pytest.raises(IncompleteScanError) as excinfo:
            sweep_missing(session, set(), skipped=3)

        assert excinfo.value.skipped == 3
        assert all(r.missing_since is None for r in session.scalars(select(Recording)))


# --- Judgement call: missing_since must not be re-stamped on re-sweep ------


def test_missing_since_is_preserved_across_repeated_sweeps(engine: Engine) -> None:
    with OrmSession(engine) as session:
        hashes = _add(session, 20)

        sweep_missing(session, set(hashes[1:]))
        session.commit()
        first_stamp = (
            session.scalars(
                select(Recording).where(Recording.audio_hash == hashes[0]),
            )
            .one()
            .missing_since
        )

        sweep_missing(session, set(hashes[1:]))
        session.commit()
        second_stamp = (
            session.scalars(
                select(Recording).where(Recording.audio_hash == hashes[0]),
            )
            .one()
            .missing_since
        )

        assert first_stamp == second_stamp
