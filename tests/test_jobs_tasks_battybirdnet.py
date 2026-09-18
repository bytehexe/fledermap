"""The one battybirdnet test that genuinely needs a real database --
`classify_battybirdnet_task` itself, which reads/writes `Recording`/
`ClassifierDetection` rows. Pure `results_to_detections` parsing (no
database) lives in `tests/test_battybirdnet_parsing.py`, mirroring the
split `tests/test_batdetect2_real_api.py` already demonstrates for the
sibling classifier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import procrastinate
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config
from fledermap.domain.codes import IdSource
from fledermap.jobs.tasks import classify_battybirdnet_task
from fledermap.store.models import (
    ClassifierDetection,
    Recording,
    Taxon,
    TaxonCode,
)

pytestmark = pytest.mark.db


def test_classify_battybirdnet_task_caches_every_detection_above_the_floor(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="p" * 64,
            path="clip.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        taxon = Taxon(rank="species", scientific_name="Myotis daubentonii")
        session.add(taxon)
        session.flush()
        session.add(
            TaxonCode(
                source="battybirdnet",
                code="Myotis daubentonii",
                taxon_id=taxon.id,
            ),
        )
        (tmp_path / "clip.wav").write_bytes(b"")
        session.commit()
        audio_hash = recording.audio_hash
        taxon_id = taxon.id

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(tmp_path,),
        battybirdnet_url="http://battybirdnet.example",
    )

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "msg": "success",
        "results": {
            # Two separate windows: results_to_detections keeps only the top-scoring
            # candidate PER WINDOW (commit ddd163c -- a window is one bat call, which
            # can only be one species), so each of these must be its own window's
            # winner to both survive, rather than two candidates of the same window.
            "0-3": [
                ["Myotis daubentonii_Daubentons bat", "0.9"],
                # Below the floor, dropped, and also not this window's winner.
                ["NOISE", "0.01"],
            ],
            "3-6": [
                # Above _BATTYBIRDNET_FLOOR (0.05), so this must be cached too --
                # proves NOISE isn't specially dropped by this function
                # (materialize_claims, not this task, is what turns it into a NOISE
                # verdict).
                ["NOISE", "0.85"],
                ["Myotis daubentonii_Daubentons bat", "0.01"],
            ],
        },
    }

    context = cast(
        "procrastinate.JobContext",
        SimpleNamespace(
            additional_context={
                "archive_roots": (tmp_path,),
                "engine": engine,
                "config": config,
            },
        ),
    )

    with patch("fledermap.jobs.tasks.requests.post", return_value=mock_response):
        classify_battybirdnet_task(context, audio_hash)

    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one()
        rows = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
            ),
        ).all()
        assert len(rows) == 2
        by_label = {r.raw_label: r for r in rows}
        assert by_label[
            "Myotis daubentonii_Daubentons bat"
        ].confidence == pytest.approx(0.9)
        assert by_label["Myotis daubentonii_Daubentons bat"].taxon_id == taxon_id
        assert by_label["NOISE"].confidence == pytest.approx(0.85)
        assert by_label["NOISE"].taxon_id is None
        assert all(r.source == IdSource.BATTYBIRDNET for r in rows)


def test_classify_battybirdnet_task_returns_early_when_url_is_unset(
    engine: Engine, tmp_path: Path
) -> None:
    """Covers the config-changed-between-enqueue-and-execution guard: if
    `battybirdnet_url` is None (classifier disabled after the job was
    deferred), the task must return without POSTing anywhere or writing any
    `classifier_detection` row."""
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="q" * 64,
            path="clip.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        (tmp_path / "clip.wav").write_bytes(b"")
        session.commit()
        audio_hash = recording.audio_hash

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(tmp_path,),
        battybirdnet_url=None,
    )

    context = cast(
        "procrastinate.JobContext",
        SimpleNamespace(
            additional_context={
                "archive_roots": (tmp_path,),
                "engine": engine,
                "config": config,
            },
        ),
    )

    with patch("fledermap.jobs.tasks.requests.post") as mock_post:
        classify_battybirdnet_task(context, audio_hash)
        mock_post.assert_not_called()

    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one()
        rows = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
            ),
        ).all()
        assert rows == []
