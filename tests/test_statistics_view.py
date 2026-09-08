# tests/test_statistics_view.py
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


def test_statistics_global_page_renders_stat_tiles_and_chart_data(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
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
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/statistics")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "1" in html  # total recordings stat tile
    assert "Eptesicus serotinus" in html  # embedded in the donut's JSON data
    assert "stats-band" in html
    assert "chart.js" in html  # vendored script tag
