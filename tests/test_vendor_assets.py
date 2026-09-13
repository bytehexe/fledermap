from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fledermap.services.vendor_assets import (
    ASSETS,
    IntegrityError,
    VendorAsset,
    ensure_vendor_assets,
    fetch_all,
    missing_assets,
    verify,
)


def test_verify_accepts_matching_hash() -> None:
    data = b"hello world"
    expected = hashlib.sha256(data).hexdigest()

    verify(data, expected)  # must not raise


def test_verify_rejects_mismatched_hash() -> None:
    with pytest.raises(IntegrityError, match="expected sha256"):
        verify(b"hello world", "0" * 64)


def test_fetch_all_writes_verified_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises the full fetch-verify-write path against a fake network
    response -- no live network call. This is a case where mocking the
    external dependency (the network) is appropriate: hitting a real CDN in a
    test run is exactly what this project's test suite avoids elsewhere."""
    payload = b"pretend this is leaflet.js"
    digest = hashlib.sha256(payload).hexdigest()
    fake_asset = VendorAsset(
        url="https://example.invalid/fake.js",
        sha256=digest,
        relative_path="fake.js",
    )

    fake_response = MagicMock()
    fake_response.read.return_value = payload
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False
    monkeypatch.setattr(urllib.request, "urlopen", lambda url: fake_response)  # noqa: ARG005

    fetch_all(tmp_path, assets=(fake_asset,))

    written = (tmp_path / "fake.js").read_bytes()
    assert written == payload


def test_fetch_all_refuses_a_tampered_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_asset = VendorAsset(
        url="https://example.invalid/fake.js",
        sha256="0" * 64,  # deliberately wrong
        relative_path="fake.js",
    )

    fake_response = MagicMock()
    fake_response.read.return_value = b"anything"
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False
    monkeypatch.setattr(urllib.request, "urlopen", lambda url: fake_response)  # noqa: ARG005

    with pytest.raises(IntegrityError):
        fetch_all(tmp_path, assets=(fake_asset,))

    assert not (tmp_path / "fake.js").exists()


def _fake_response(payload: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = payload
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_missing_assets_returns_only_what_is_absent(tmp_path: Path) -> None:
    present = VendorAsset(url="https://x/a.js", sha256="0" * 64, relative_path="a.js")
    absent = VendorAsset(url="https://x/b.js", sha256="0" * 64, relative_path="b.js")
    (tmp_path / "a.js").write_bytes(b"already here")

    result = missing_assets(tmp_path, assets=(present, absent))

    assert result == (absent,)


def test_ensure_vendor_assets_fetches_nothing_when_cache_is_warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of treating `static_root` as a cache (`Config.static_root`'s
    docstring): a warm cache means `serve` can call this on every startup
    without ever touching the network."""
    asset = VendorAsset(url="https://x/a.js", sha256="0" * 64, relative_path="a.js")
    (tmp_path / "a.js").write_bytes(b"already here")

    def fail_if_called(_url: str) -> MagicMock:
        pytest.fail("network should not be touched when nothing is missing")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)

    fetched = ensure_vendor_assets(tmp_path, assets=(asset,))

    assert fetched == ()


def test_ensure_vendor_assets_fetches_only_the_missing_ones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"pretend this is leaflet.js"
    digest = hashlib.sha256(payload).hexdigest()
    present = VendorAsset(url="https://x/a.js", sha256="0" * 64, relative_path="a.js")
    absent = VendorAsset(url="https://x/b.js", sha256=digest, relative_path="b.js")
    (tmp_path / "a.js").write_bytes(b"already here, untouched")

    calls = []

    def fake_urlopen(url: str) -> MagicMock:
        calls.append(url)
        return _fake_response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    fetched = ensure_vendor_assets(tmp_path, assets=(present, absent))

    assert fetched == (absent,)
    assert calls == ["https://x/b.js"]
    assert (tmp_path / "a.js").read_bytes() == b"already here, untouched"
    assert (tmp_path / "b.js").read_bytes() == payload


def test_assets_includes_chart_js() -> None:
    relative_paths = {asset.relative_path for asset in ASSETS}
    assert "chart.js" in relative_paths


def test_assets_includes_every_relative_path_exactly_once() -> None:
    """A real regression risk right after appending 21 new entries by hand: a copy-paste
    duplicate relative_path would silently overwrite one icon file with another's bytes on
    fetch, with no error anywhere in this module."""
    relative_paths = [asset.relative_path for asset in ASSETS]
    assert len(relative_paths) == len(set(relative_paths))


def test_assets_includes_every_icon_the_app_uses() -> None:
    """Pins the exact (icon, style) inventory design spec 2026-09-13 requires -- catches an
    icon silently dropped from ASSETS (icon() would then raise IconNotFoundError at render
    time, but this test catches it before that ever ships)."""
    relative_paths = {asset.relative_path for asset in ASSETS}
    expected = {
        "icons/outline/flag.svg",
        "icons/filled/flag.svg",
        "icons/outline/star.svg",
        "icons/filled/star.svg",
        "icons/filled/sun.svg",
        "icons/filled/moon.svg",
        "icons/outline/device-desktop.svg",
        "icons/outline/menu-2.svg",
        "icons/outline/lock.svg",
        "icons/filled/lock.svg",
        "icons/filled/player-play.svg",
        "icons/filled/player-pause.svg",
        "icons/filled/player-skip-back.svg",
        "icons/outline/rotate.svg",
        "icons/outline/chevron-left.svg",
        "icons/outline/chevron-right.svg",
        "icons/outline/alert-triangle.svg",
        "icons/outline/info-circle.svg",
        "icons/outline/check.svg",
        "icons/outline/chevron-down.svg",
        "icons/outline/x.svg",
    }
    assert expected <= relative_paths
