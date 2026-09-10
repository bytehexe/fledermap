from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Taxon
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_reviews_page_lists_flagged_recordings(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
        recording_id = r.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Pipistrellus pipistrellus" in html
    assert "a" * 64 in html
    assert "1 recording flagged for review" in html
    assert f"review={recording_id}" in html


def test_reviews_page_zero_flagged(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "0 recordings flagged for review" in html
    assert "Start reviewing" not in html
