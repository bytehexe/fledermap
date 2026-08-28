from __future__ import annotations

import pytest

from fledermap.services import site_naming


def test_poiidx_connection_kwargs_parses_a_well_formed_url() -> None:
    kwargs = site_naming._poiidx_connection_kwargs(
        "postgresql://poiidx_user:s3cret@localhost:5432/poiidx_bats_db",
    )
    assert kwargs == {
        "host": "localhost",
        "port": 5432,
        "user": "poiidx_user",
        "password": "s3cret",
        "database": "poiidx_bats_db",
    }


def test_poiidx_connection_kwargs_defaults_port_to_5432() -> None:
    kwargs = site_naming._poiidx_connection_kwargs(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )
    assert kwargs["port"] == 5432


def test_poiidx_connection_kwargs_rejects_a_url_with_no_password() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user@localhost/poiidx_bats_db",
        )


def test_poiidx_connection_kwargs_rejects_a_url_with_no_database() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user:s3cret@localhost/",
        )


def test_load_filter_config_returns_the_expected_symbols() -> None:
    config = site_naming._load_filter_config()
    symbols = {entry["symbol"] for entry in config}
    assert symbols == {
        "city",
        "town",
        "village",
        "suburb",
        "forest_or_park",
        "water_body",
    }


def test_ensure_connected_calls_poiidx_init_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "_connected", False)
    calls: list[dict[str, object]] = []

    def fake_init(*, filter_config: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(site_naming.poiidx, "init", fake_init)

    site_naming.ensure_connected(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )
    site_naming.ensure_connected(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )

    assert len(calls) == 1
    assert calls[0]["database"] == "poiidx_bats_db"
