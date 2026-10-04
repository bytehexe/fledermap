from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.services import iucn_ranges
from fledermap.services.iucn_ranges import RangePolygon
from fledermap.store.models import (
    Identification,
    Recording,
    SpeciesRangeFetch,
    Taxon,
    TaxonCode,
)
from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]

SQUARE = RangePolygon(
    presence=1,
    origin=1,
    wkt="MULTIPOLYGON(((0 0, 0 1, 1 1, 1 0, 0 0)))",
)


def _seed(engine: Engine, *, lon: float) -> str:
    audio_hash = "ab" * 32
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        session.add(
            TaxonCode(
                source=RANGE_CODE_SOURCE,
                code="Pipistrellus pipistrellus",
                taxon_id=taxon.id,
            ),
        )
        recording = Recording(
            audio_hash=audio_hash,
            path="a.wav",
            recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
            duration_s=0.5,
            samplerate_hz=256_000,
            geom=WKTElement(f"POINT({lon} 0.5)", srid=4326),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.BATDETECT2,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        iucn_ranges.store_range(
            session, taxon.id, "Pipistrellus pipistrellus", [SQUARE]
        )
        session.commit()
    return audio_hash


def _client_get(
    engine: Engine,
    tmp_path: Path,
    url: str,
    *,
    iucn_ranges_enabled: bool = False,
) -> str:
    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        iucn_ranges_enabled=iucn_ranges_enabled,
    )
    response = app.test_client().get(url)
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("lon", "css_class", "text"),
    [
        (0.5, "range-inside", "Inside the IUCN range"),
        (1.5, "range-near", "km outside the IUCN range"),
        (6.0, "range-far", "km outside the IUCN range"),
    ],
)
def test_details_page_shows_the_range_cell_for_each_state(
    engine: Engine, tmp_path: Path, lon: float, css_class: str, text: str
) -> None:
    audio_hash = _seed(engine, lon=lon)

    html = _client_get(
        engine, tmp_path, f"/recordings/{audio_hash}", iucn_ranges_enabled=True
    )

    assert "range-column" in html
    assert css_class in html
    assert text in html
    assert "release 2022-2" in html
    assert "non-commercial" in html


def test_the_iucn_column_is_absent_when_the_flag_is_off(
    engine: Engine, tmp_path: Path
) -> None:
    audio_hash = _seed(engine, lon=0.5)

    html = _client_get(engine, tmp_path, f"/recordings/{audio_hash}")

    assert "range-column" not in html
    assert "range-cell" not in html
    assert "release 2022-2" not in html


def test_no_range_geometry_reaches_the_page(engine: Engine, tmp_path: Path) -> None:
    """IUCN T&C §4: the page may carry a verdict and a distance, never a polygon."""
    audio_hash = _seed(engine, lon=0.5)

    html = _client_get(
        engine, tmp_path, f"/recordings/{audio_hash}", iucn_ranges_enabled=True
    )

    assert "MULTIPOLYGON" not in html.upper()
    assert "0 0, 0 1, 1 1" not in html  # the synthetic square's vertices


def test_a_species_not_fetched_yet_gets_the_pending_cell(
    engine: Engine, tmp_path: Path
) -> None:
    audio_hash = _seed(engine, lon=0.5)
    with OrmSession(engine) as session:
        session.execute(delete(SpeciesRangeFetch))
        session.commit()

    html = _client_get(
        engine, tmp_path, f"/recordings/{audio_hash}", iucn_ranges_enabled=True
    )

    assert "range-pending" in html
    assert "range-no_data" not in html
    assert "IUCN range not fetched yet" in html


def test_a_species_without_an_iucn_name_gets_the_no_data_cell(
    engine: Engine, tmp_path: Path
) -> None:
    audio_hash = _seed(engine, lon=0.5)
    with OrmSession(engine) as session:
        session.execute(delete(TaxonCode))
        session.commit()

    html = _client_get(
        engine, tmp_path, f"/recordings/{audio_hash}", iucn_ranges_enabled=True
    )

    assert "range-no_data" in html
    assert "range-pending" not in html


def test_the_drawer_panel_shows_the_column_too(engine: Engine, tmp_path: Path) -> None:
    """Drawer/detail parity: both views include the same matrix partial."""
    audio_hash = _seed(engine, lon=0.5)

    html = _client_get(
        engine,
        tmp_path,
        f"/recordings/{audio_hash}/panel",
        iucn_ranges_enabled=True,
    )

    assert "range-inside" in html
