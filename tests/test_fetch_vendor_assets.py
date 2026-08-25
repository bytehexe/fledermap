from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.fetch_vendor_assets import IntegrityError, VendorAsset, fetch_all, verify


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
