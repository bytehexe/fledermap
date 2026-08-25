"""Fetch pinned-version JS/CSS assets into the configured static root
(design spec section 5, decision P4-4).

Run manually at setup/deploy time (documented in CLAUDE.md's Environment
gotchas) -- needs network access, so it is NOT part of the test suite's own
execution path (tests/test_fetch_vendor_assets.py exercises the
verify/fetch/write logic against a fake response instead). Each asset's
SHA-256 is checked against the downloaded bytes before anything is written;
a mismatch means the CDN served something other than what was pinned when
this script was last updated, and nothing is written for that asset.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fledermap.config import resolve_static_root


@dataclass(frozen=True)
class VendorAsset:
    url: str
    sha256: str
    relative_path: str  # where it lands under <static_root>/vendor/


# Fetched and hashed directly against unpkg.com before this plan was written
# -- not invented. Leaflet's own images/ files are needed because leaflet.css
# references three of them by relative URL, and L.Icon.Default (Leaflet's
# default marker) needs the other two -- a well-known gotcha for anyone
# serving Leaflet without its own build/CDN setup.
ASSETS: tuple[VendorAsset, ...] = (
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
        sha256="db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
        relative_path="leaflet.js",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
        sha256="a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
        relative_path="leaflet.css",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        sha256="574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437",
        relative_path="images/marker-icon.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        sha256="00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528",
        relative_path="images/marker-icon-2x.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
        sha256="264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da",
        relative_path="images/marker-shadow.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/layers.png",
        sha256="1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6",
        relative_path="images/layers.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png",
        sha256="066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf",
        relative_path="images/layers-2x.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js",
        sha256="1e4e1d22972a3926f48598e0caf14e3fe7049835d428a344fed4f9e3665b3508",
        relative_path="leaflet.markercluster.js",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css",
        sha256="614dea0a98ff3f4ead74f04918f6b1d1b9ba435c25b5fc23b21a394d1e3e4d87",
        relative_path="MarkerCluster.css",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css",
        sha256="61258232d98d64dc2a7b1e02130d67421bc5b9bda5994eef70228ff97570c170",
        relative_path="MarkerCluster.Default.css",
    ),
    VendorAsset(
        url="https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js",
        sha256="491955cd1810747d7d7b9ccb936400afb760e06d25d53e4572b64b6563b2784e",
        relative_path="htmx.min.js",
    ),
    VendorAsset(
        url="https://unpkg.com/alpinejs@3.14.8/dist/cdn.min.js",
        sha256="b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643",
        relative_path="alpine.min.js",
    ),
)


class IntegrityError(Exception):
    """A downloaded asset's SHA-256 didn't match what was pinned."""


def verify(data: bytes, expected_sha256: str) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        msg = f"expected sha256 {expected_sha256}, got {digest}"
        raise IntegrityError(msg)


def fetch_all(vendor_dir: Path, assets: tuple[VendorAsset, ...] = ASSETS) -> None:
    for asset in assets:
        with urllib.request.urlopen(asset.url) as response:
            data = response.read()
        verify(data, asset.sha256)
        dest = vendor_dir / asset.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def main() -> int:
    vendor_dir = resolve_static_root() / "vendor"
    fetch_all(vendor_dir)
    print(f"fetched {len(ASSETS)} assets into {vendor_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
