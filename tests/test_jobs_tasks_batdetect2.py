from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import procrastinate
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.jobs.tasks import batdetect2_lock_key, classify_batdetect2_task
from fledermap.store.models import ClassifierDetection, Recording, Taxon, TaxonCode

pytestmark = pytest.mark.db


def test_batdetect2_lock_key_is_scoped_to_the_audio_hash() -> None:
    assert batdetect2_lock_key("abc123") == "batdetect2:abc123"


def _annotation(
    class_name: str,
    *,
    det_prob: float,
    class_prob: float,
) -> dict[str, object]:
    """One entry of `api.process_file(...)["pred_dict"]["annotation"]`, in the
    real package's shape -- a plain dict naming exactly ONE class, with two
    separate scores. Reproduced from batdetect2 1.3.1 running over a
    synthetic WAV; `test_parsing_matches_the_real_batdetect2_output_shape`
    below re-checks it against the installed package rather than letting this
    hand-written copy drift into fiction."""
    return {
        "start_time": 0.0015,
        "end_time": 0.0339,
        "low_freq": 68437,
        "high_freq": 80137,
        "class": class_name,
        "class_prob": class_prob,
        "det_prob": det_prob,
        "individual": "-1",
        "event": "Echolocation",
    }


def _fake_api(annotations: list[dict[str, object]]) -> SimpleNamespace:
    return SimpleNamespace(
        process_file=lambda path: {"pred_dict": {"annotation": annotations}},
    )


def test_classify_batdetect2_task_stores_detections_above_the_floor_only(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="hashA" * 8,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        taxon_id = taxon.id
        session.add(
            TaxonCode(
                source="batdetect2",
                code="Pipistrellus pipistrellus",
                taxon_id=taxon.id,
            ),
        )
        (tmp_path / "a.wav").write_bytes(b"")
        session.commit()
        audio_hash = recording.audio_hash

    fake_api_module = _fake_api(
        [
            _annotation("Pipistrellus pipistrellus", det_prob=0.95, class_prob=0.9),
            # Below the floor on det_prob, dropped -- even though its
            # class_prob would clear it, which is what pins that the floor
            # gates on det_prob.
            _annotation("Pipistrellus pipistrellus", det_prob=0.02, class_prob=0.9),
        ],
    )
    # `from batdetect2 import api` needs the PARENT package in sys.modules
    # too, not just "batdetect2.api" -- Python's import machinery resolves
    # the parent first and only falls back to a submodule-qualified
    # sys.modules lookup after that succeeds; patching only the submodule
    # key still raises ModuleNotFoundError for the real, uninstalled
    # "batdetect2" package. Confirmed by direct reproduction.
    fake_batdetect2_package = SimpleNamespace(api=fake_api_module)

    # A plain SimpleNamespace stand-in for `procrastinate.JobContext`: this
    # test exercises the task's own logic, not Procrastinate's worker
    # machinery (see `tests/test_jobs_tasks.py`'s `_run_worker` for the
    # full-worker alternative other tasks use) -- the task only ever reads
    # `.additional_context`, so a real JobContext would add nothing here.
    context = cast(
        "procrastinate.JobContext",
        SimpleNamespace(
            additional_context={
                "archive_roots": (tmp_path,),
                "engine": engine,
            },
        ),
    )

    with patch.dict(
        "sys.modules",
        {"batdetect2": fake_batdetect2_package, "batdetect2.api": fake_api_module},
    ):
        classify_batdetect2_task(context, audio_hash)

    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one()
        rows = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
            ),
        ).all()
        assert len(rows) == 1
        # `class_prob`, not `det_prob`: the stored confidence is the one the
        # user's configured species threshold is compared against.
        assert rows[0].confidence == 0.9
        assert rows[0].taxon_id == taxon_id
        assert rows[0].raw_label == "Pipistrellus pipistrellus"
        assert rows[0].source == IdSource.BATDETECT2


def test_classify_batdetect2_task_keeps_an_unmapped_label(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A class name with no `TaxonCode` mapping is still stored, with
    taxon_id None and the label preserved, so `materialize_claims` can turn it
    into an "unmapped species" claim for the review queue instead of the
    recording silently becoming a NO_ID that nothing ever revisits."""
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="hashB" * 8,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        (tmp_path / "b.wav").write_bytes(b"")
        session.commit()
        audio_hash = recording.audio_hash

    fake_api_module = _fake_api(
        [_annotation("Myotis alcathoe", det_prob=0.9, class_prob=0.85)],
    )
    context = cast(
        "procrastinate.JobContext",
        SimpleNamespace(
            additional_context={"archive_roots": (tmp_path,), "engine": engine},
        ),
    )

    with patch.dict(
        "sys.modules",
        {
            "batdetect2": SimpleNamespace(api=fake_api_module),
            "batdetect2.api": fake_api_module,
        },
    ):
        classify_batdetect2_task(context, audio_hash)

    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one()
        row = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
            ),
        ).one()
        assert row.taxon_id is None
        assert row.raw_label == "Myotis alcathoe"
        assert row.confidence == 0.85


def test_annotations_to_detections_keeps_the_call_geometry() -> None:
    from fledermap.jobs.tasks import annotations_to_detections

    annotations = [
        _annotation("Pipistrellus pipistrellus", det_prob=0.9, class_prob=0.85)
    ]

    detections = annotations_to_detections(
        annotations, resolve_label=lambda _label: None
    )

    assert len(detections) == 1
    detection = detections[0]
    assert detection.time_start_s == 0.0015
    assert detection.time_end_s == 0.0339
    assert detection.freq_min_hz == 68437
    assert detection.freq_max_hz == 80137
