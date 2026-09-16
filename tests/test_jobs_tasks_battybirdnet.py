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
from fledermap.jobs.tasks import (
    battybirdnet_lock_key,
    classify_battybirdnet_task,
    results_to_detections,
)
from fledermap.store.models import (
    ClassifierDetection,
    Recording,
    Taxon,
    TaxonCode,
)

pytestmark = pytest.mark.db


def test_battybirdnet_lock_key_is_namespaced_by_audio_hash() -> None:
    assert battybirdnet_lock_key("abc") == "battybirdnet:abc"


def test_results_to_detections_flattens_every_segment_above_the_floor() -> None:
    resolved = {"Myotis daubentonii": 7, "NOISE": None}
    results = {
        "0-3": [
            ["Myotis daubentonii_Daubentons bat", "0.9"],
            ["NOISE", "0.02"],
        ],
        "3-6": [
            ["NOISE", "0.85"],
            ["Myotis daubentonii_Daubentons bat", "0.01"],
        ],
    }

    detections = results_to_detections(
        results, lambda label: resolved.get(label.split("_")[0])
    )

    confidences = {(d.raw_label, round(d.confidence, 2)) for d in detections}
    assert ("Myotis daubentonii_Daubentons bat", 0.9) in confidences
    assert ("NOISE", 0.85) in confidences
    # Below _BATTYBIRDNET_FLOOR, dropped:
    assert not any(c == 0.02 or c == 0.01 for _, c in confidences)


def test_results_to_detections_resolves_the_scientific_name_prefix_only() -> None:
    def resolve_label(label: str) -> int | None:
        scientific_name = label.split("_", 1)[0]
        return 42 if scientific_name == "Myotis daubentonii" else None

    results = {"0-3": [["Myotis daubentonii_Daubentons bat", "0.9"]]}

    detections = results_to_detections(results, resolve_label)

    assert detections[0].taxon_id == 42
    assert detections[0].raw_label == "Myotis daubentonii_Daubentons bat"


def test_results_to_detections_marks_noise_as_unresolved_via_sentinel() -> None:
    """results_to_detections itself never calls sentinel_verdict -- that
    happens downstream in materialize_claims (Task 1). This test only
    proves a bare "NOISE" label is preserved verbatim as raw_label with
    taxon_id from whatever resolve_label returns for it (materialize_claims
    is what turns it into a NOISE verdict, not this parsing function)."""
    results = {"0-3": [["NOISE", "0.9"]]}

    detections = results_to_detections(results, lambda label: None)

    assert detections[0].raw_label == "NOISE"
    assert detections[0].taxon_id is None


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
            "0-3": [
                ["Myotis daubentonii_Daubentons bat", "0.9"],
                # Above _BATTYBIRDNET_FLOOR (0.05), so this must be cached
                # too -- proves NOISE isn't specially dropped by this
                # function (materialize_claims, not this task, is what
                # turns it into a NOISE verdict).
                ["NOISE", "0.85"],
                # Below the floor, dropped.
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
