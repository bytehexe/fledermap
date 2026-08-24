"""The suite builds its schema with `create_all`, so nothing else exercises the
migration. A migration that has drifted from the models is invisible until a
real deployment runs it (review item 4)."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.exc import IntegrityError

from alembic import command
from fledermap.domain.codes import IdSource, MergeResolution, SessionKind, Verdict
from fledermap.store.db import make_engine
from fledermap.store.models import Base

pytestmark = pytest.mark.db

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated_engine(postgis_url: str) -> Iterator[Engine]:
    """A database whose schema came from `alembic upgrade head`, not `create_all`."""
    eng = make_engine(postgis_url)
    with eng.begin() as conn:
        # Wipe whatever another test left, postgis and alembic's own version
        # table included, so the migration runs against genuinely nothing.
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    # Deliberately NOT Config("alembic.ini"): env.py calls fileConfig() whenever
    # the config carries a file name, and fileConfig defaults to
    # disable_existing_loggers=True — which would silently disable every logger
    # created before this fixture runs, at a random point in the session.
    # `script_location` and `sqlalchemy.url` are the only settings upgrade needs.
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", postgis_url)
    command.upgrade(cfg, "head")

    yield eng
    eng.dispose()


def _enum_check_constraints() -> set[tuple[str, str]]:
    """(table, constraint name) for every CHECK a non-native `Enum` column emits.

    SQLAlchemy marks these constraints `_type_bound`, and alembic's metadata
    side drops type-bound constraints outright
    (`sqla_compat.all_table_check_constraints`) while the reflected side has no
    such concept. Every one of them would therefore be reported as
    `remove_constraint` on a perfectly faithful migration. Excluding them by
    exact name — rather than excluding check constraints wholesale — keeps any
    other check constraint under comparison.

    `test_migrated_verdict_check_is_enforced` covers what this exclusion drops.
    """
    return {
        (table.name, col.type.name)
        for table in Base.metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, SAEnum)
        and not col.type.native_enum
        and col.type.create_constraint
        and col.type.name
    }


def _comparable(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if type_ == "table":
        # PostGIS brings `spatial_ref_sys` with it; it is not ours to compare.
        return name in Base.metadata.tables
    if type_ == "check_constraint":
        table = getattr(obj, "table", None)
        return (getattr(table, "name", None), name) not in _enum_check_constraints()
    return True


def test_migration_creates_the_postgis_extension(migrated_engine: Engine) -> None:
    """`upgrade()` runs against a schema with no postgis; the geography column
    would fail outright if the extension were left to an external prerequisite."""
    with migrated_engine.connect() as conn:
        installed = conn.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'postgis'")
        )
    assert installed == 1


def test_migration_matches_the_models(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "target_metadata": Base.metadata,
                "include_object": _comparable,
            },
        )
        diffs = compare_metadata(context, Base.metadata)

    assert diffs == [], f"alembic upgrade head has drifted from models.py: {diffs}"


def test_migrated_verdict_check_is_enforced(migrated_engine: Engine) -> None:
    """`verdict` is a closed vocabulary, and its CHECK is the one constraint
    `_comparable` excludes from the drift comparison. Assert it directly."""
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                " VALUES ('m' || repeat('0', 63), 'x.wav', now(), '{}'::jsonb)"
            )
        )

    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO identification (recording_id, source, verdict)"
                " SELECT id, 'manual', 'not_a_verdict' FROM recording"
            )
        )


def test_migrated_verdict_check_accepts_every_verdict(migrated_engine: Engine) -> None:
    """The other half of the exclusion `_comparable` makes. Rejecting a bogus
    value proves the CHECK exists; only this proves it still matches `Verdict`.
    A member added to the enum without a migration would otherwise pass both the
    drift comparison (excluded) and the rejection test (still rejects garbage)."""
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                " VALUES ('v' || repeat('0', 63), 'x.wav', now(), '{}'::jsonb)"
            )
        )
        for verdict in Verdict:
            # raw_label must differ per row: uq_identification_source_claim is
            # (recording_id, source, source_version, raw_label) with
            # nulls_not_distinct, and `verdict` is not part of it.
            conn.execute(
                text(
                    "INSERT INTO identification"
                    " (recording_id, source, verdict, raw_label)"
                    " SELECT id, 'manual', :verdict, :label FROM recording"
                ),
                {"verdict": verdict.value, "label": verdict.value},
            )
        stored = conn.scalar(text("SELECT count(*) FROM identification"))

    assert stored == len(Verdict)


def test_migrated_kind_check_is_enforced(migrated_engine: Engine) -> None:
    """`kind` is a closed two-value vocabulary (phase 2, task 5) — the one
    constraint `_comparable` excludes from the drift comparison."""
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'not_a_kind')"
            )
        )


def test_migrated_kind_check_accepts_every_kind(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        for kind in SessionKind:
            conn.execute(
                text(
                    "INSERT INTO session (started_at, ended_at, kind)"
                    " VALUES (now(), now(), :kind)"
                ),
                {"kind": kind.value},
            )
        stored = conn.scalar(text("SELECT count(*) FROM session"))

    assert stored == len(SessionKind)


def test_migrated_resolution_check_is_enforced(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                " VALUES ('r' || repeat('0', 63), 'x.wav', now(), '{}'::jsonb)"
            )
        )

    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session_merge_proposal"
                " (session_a_id, session_b_id, bridging_recording_id, detected_at,"
                "  resolution)"
                " SELECT s1.id, s2.id, r.id, now(), 'not_a_resolution'"
                " FROM session s1, session s2, recording r"
                " WHERE s1.id != s2.id LIMIT 1"
            )
        )


def test_migrated_resolution_check_accepts_every_resolution(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                " VALUES ('s' || repeat('0', 63), 'x.wav', now(), '{}'::jsonb)"
            )
        )
        for resolution in MergeResolution:
            conn.execute(
                text(
                    "INSERT INTO session_merge_proposal"
                    " (session_a_id, session_b_id, bridging_recording_id,"
                    "  detected_at, resolution)"
                    " SELECT s1.id, s2.id, r.id, now(), :resolution"
                    " FROM session s1, session s2, recording r"
                    " WHERE s1.id != s2.id LIMIT 1"
                ),
                {"resolution": resolution.value},
            )
        stored = conn.scalar(text("SELECT count(*) FROM session_merge_proposal"))
        # NULL resolution (still open) must also be accepted by the CHECK.
        conn.execute(
            text(
                "INSERT INTO session_merge_proposal"
                " (session_a_id, session_b_id, bridging_recording_id, detected_at)"
                " SELECT s1.id, s2.id, r.id, now()"
                " FROM session s1, session s2, recording r"
                " WHERE s1.id != s2.id LIMIT 1"
            )
        )

    assert stored == len(MergeResolution)


def _migration_idsource_literal() -> list[str]:
    """The literal `source` enum values `0001_initial.py` hard-codes.

    `compare_metadata` cannot see drift here (whole-branch review, finding 2):
    `Identification.source` is `native_enum=False, create_constraint=False`
    (models.py), so both the model and the migration erase to a plain
    `VARCHAR(32)` with no CHECK — there is nothing for a schema diff to
    compare. `emt.manual` was added to `IdSource` in Task 11's fix round,
    after this migration was written, and stayed missing from the migration
    with nothing to catch it. `verdict` has an equivalent test
    (`test_migrated_verdict_check_accepts_every_verdict`, above) because it
    DOES get a CHECK; `source` needs a different mechanism because it
    deliberately doesn't.

    Read via `ast`, not by importing the migration module and calling
    `upgrade()`: the values are needed at collection time, without a
    database, and parsing avoids ever executing DDL to answer a question
    about source code.
    """
    tree = ast.parse(
        (PROJECT_ROOT / "alembic" / "versions" / "0001_initial.py").read_text(),
    )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Enum"
            and any(
                kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "idsource"
                for kw in node.keywords
            )
        ):
            return [
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
    msg = "no sa.Enum(name='idsource') call found in 0001_initial.py"
    raise AssertionError(msg)


def test_migration_idsource_literal_matches_the_model() -> None:
    """Static, no database: the migration's hard-coded `source` vocabulary
    must list every `IdSource` member. Mutation-test this by adding a member
    to `IdSource` without touching the migration — it must fail."""
    assert _migration_idsource_literal() == [s.value for s in IdSource]
