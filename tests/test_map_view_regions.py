from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Region
from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def _seed(engine: Engine, *, distinct: bool = True) -> None:
    with OrmSession(engine) as session:
        session.add(Region(id="germany", name="Germany", is_distinct=distinct))
        session.add(Region(id="bremen", name="Bremen", is_distinct=False))
        session.commit()


def _page(engine: Engine, tmp_path: Path, *, enabled: bool) -> str:
    app = create_app(
        engine, tmp_path / "static", tmp_path / "media", regions_enabled=enabled
    )
    return app.test_client().get("/").get_data(as_text=True)


def test_region_dropdown_lists_only_distinct_regions(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine)
    html = _page(engine, tmp_path, enabled=True)
    assert 'name="region"' in html
    assert '<option value="germany">Germany</option>' in html
    assert "Bremen" not in html


def test_region_dropdown_sits_after_the_taxon_group_and_before_source(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine)
    html = _page(engine, tmp_path, enabled=True)
    assert html.index('id="taxon-exclude"') < html.index('name="region"')
    assert html.index('name="region"') < html.index('name="source"')


def test_region_dropdown_is_omitted_when_poiidx_is_not_configured(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine)
    assert 'name="region"' not in _page(engine, tmp_path, enabled=False)


def test_region_dropdown_is_omitted_when_no_region_is_stored(
    engine: Engine, tmp_path: Path
) -> None:
    assert 'name="region"' not in _page(engine, tmp_path, enabled=True)


def test_region_dropdown_is_omitted_when_no_region_is_distinct(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine, distinct=False)
    assert 'name="region"' not in _page(engine, tmp_path, enabled=True)
