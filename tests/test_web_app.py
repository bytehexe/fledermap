from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_create_app_registers_vendor_static_blueprint(
    tmp_path: Path,
    engine: Engine,
) -> None:
    vendor_dir = tmp_path / "static" / "vendor"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "leaflet.js").write_text("/* fake */")

    app = create_app(engine, tmp_path / "static")
    client = app.test_client()

    response = client.get("/static/vendor/leaflet.js")

    assert response.status_code == 200
    assert response.data == b"/* fake */"


def test_create_app_stores_the_engine_on_config(tmp_path: Path, engine: Engine) -> None:
    app = create_app(engine, tmp_path / "static")

    assert app.config["ENGINE"] is engine
