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


def test_reviews_page_start_reviewing_label_reflects_the_capped_snapshot_size(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A flagged count over MAX_REVIEW_SNAPSHOT (500) must not make the
    "Start reviewing (N)" button lie about how many recordings the review=
    snapshot actually reaches -- it should read the same 500 the truncation
    banner reports, not the untruncated total (this was the Important
    finding from code review: the button previously showed `count`, not
    `len(snapshot_ids)`)."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        for i in range(501):
            r = Recording(
                audio_hash=f"{i:064x}",
                path=f"{i}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                flagged_for_review=True,
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
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "501 recordings flagged for review" in html
    assert "Start reviewing (500)" in html
    assert "Start reviewing (501)" not in html
    assert "Showing the first 500" in html


def test_reviews_page_includes_unidentified_flagged_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A recording flagged for review with no identification (unidentified)
    must appear on the reviews page, not be filtered out by the verdict
    filter. The manual flag is independent of species classification."""
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            flagged_for_review=True,
        )
        session.add(r)
        session.commit()
        recording_id = r.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "1 recording flagged for review" in html
    assert "b" * 64 in html
    assert f"review={recording_id}" in html
    # The species column must use recording_headline's real "unidentified"
    # label for a recording with no identification at all -- not the view's
    # old hand-rolled ternary, which wrongly fell back to "unmapped species"
    # for every non-simple case (unidentified, multi-species, NOISE, NO_ID).
    assert ">unidentified<" in html
    assert "unmapped species" not in html
