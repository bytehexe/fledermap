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


def _fake_detection(class_name: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        detection_score=score,
        class_scores={class_name: score},
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

    fake_api_module = SimpleNamespace(
        process_file=lambda path: SimpleNamespace(
            detections=[
                _fake_detection("Pipistrellus pipistrellus", 0.9),
                _fake_detection(
                    "Pipistrellus pipistrellus", 0.02
                ),  # below floor, dropped
            ],
        ),
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
        assert rows[0].confidence == 0.9
        assert rows[0].taxon_id == taxon_id
        assert rows[0].source == IdSource.BATDETECT2
