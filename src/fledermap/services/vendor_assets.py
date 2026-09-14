"""Fetch pinned-version JS/CSS assets into the configured static root
(design spec section 5, decision P4-4).

Lives in the installed package, not `scripts/` -- `scripts/` is dev-only
tooling (git hooks) that never ships in a built wheel or sdist, so code that
belongs to a real deployment (this does: `serve` needs it to run at all)
cannot live there. `ensure_vendor_assets` is what `serve` calls automatically
on startup when assets are missing (`static_root` is a cache, by design --
see `Config.static_root`'s docstring -- and a cache that can't repopulate
itself on demand isn't one); `fetch_all` unconditionally re-fetches every
given asset and backs the explicit `fledermap fetch-assets` command, for
pre-warming a deployment or an offline/air-gapped install ahead of time.

Each asset's SHA-256 is checked against the downloaded bytes before anything
is written; a mismatch means the CDN served something other than what was
pinned when this module was last updated, and nothing is written for that
asset. Auto-fetching on a cache miss doesn't relax this -- it's the same
verified fetch either way, just triggered automatically instead of by hand.
"""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path


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
    # Chart.js for the statistics feature
    VendorAsset(
        url="https://unpkg.com/chart.js@4.4.6/dist/chart.umd.js",
        sha256="3850656abbdc319141e6e8ce8eacde2622fc767c30e20d81704af2bf3159f92d",
        relative_path="chart.js",
    ),
    # Tabler Icons (MIT, https://tabler.io/icons), pinned 3.46.0 -- design spec
    # docs/superpowers/specs/2026-09-13-fledermap-icon-set-design.md. Fetched and hashed directly
    # against unpkg.com before this plan was written -- not invented. Only the specific
    # (icon, style) pairs this app actually uses, never the whole library.
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/flag.svg",
        sha256="eafc36e1306bc87d9ae65fd6a75c5aff61d0ceb5ff852dbf94a03fa2068a2f89",
        relative_path="icons/outline/flag.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/flag.svg",
        sha256="012892f51a80299cff80e2ac96a8e64adebb72027e7614b86839c9f098971791",
        relative_path="icons/filled/flag.svg",
    ),
    # Shown instead of the outline flag when a recording is auto-flagged (a computed
    # review_flags reason) but not manually flagged -- no filled variant needed, since a
    # manual flag always wins and renders the filled flag icon regardless of auto status.
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/flag-cog.svg",
        sha256="8093c597fcdc8667bca441c622d8c8e82b976595586e5c7871f2563b5b98a196",
        relative_path="icons/outline/flag-cog.svg",
    ),
    # The Denoise toggle's icon (design spec
    # docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md) -- no filled
    # variant exists in Tabler's set, so the pressed (ON) state is signalled by CSS alone
    # (the button's own background/color change), the same way the toolbar's other
    # icon-less toggle buttons (Default/Ruler/TE/HET) already work.
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/noise-reduction.svg",
        sha256="a1f2f9e11fb95a640691786c54041bf65e3d9a181280bbd18440b48589a52151",
        relative_path="icons/outline/noise-reduction.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/star.svg",
        sha256="9903f4359966f48225710d78088db8fdfbc30eb82596a4bb28bc49b56399465d",
        relative_path="icons/outline/star.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/star.svg",
        sha256="50ade4fd4b67aff7eca0a9f2ad0e8c52ef4cea659be4a1fff8f03a1a9647aec7",
        relative_path="icons/filled/star.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/sun.svg",
        sha256="91c6966504af4b913d7e231804b91a9bf1bce5b42ba5bc9d08c8e52cf0ebfb0c",
        relative_path="icons/filled/sun.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/moon.svg",
        sha256="86f4dd8822aec2c6b9ea9a9286371136587ec0c748cf52d815e3c0ff934879d2",
        relative_path="icons/filled/moon.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/device-desktop.svg",
        sha256="a18c2a33a20de14bacbff9df97b7896006fb166379da84eb12c304abbd803286",
        relative_path="icons/outline/device-desktop.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/menu-2.svg",
        sha256="a7630e8fc37c090e8d665c8739ea838566db4bcf79c7710a554ace3bf4733e04",
        relative_path="icons/outline/menu-2.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/lock.svg",
        sha256="748ef36a7e3b7a4dc2b44a16576dc60123968a6c8184155015f85cfa442ce7d7",
        relative_path="icons/outline/lock.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/lock.svg",
        sha256="b8b0946430cc541c8bb8e473cab0e57eaf5d06f31fd46e5e21f7dd47b6ce355d",
        relative_path="icons/filled/lock.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/player-play.svg",
        sha256="2ba08da4e3f0d78b6a957e355c948bf8d5cd4241286aa8bb48a54d97e767929b",
        relative_path="icons/filled/player-play.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/player-pause.svg",
        sha256="bb1386fd0e04b18f46d0399610b234cf7bca059638a3d3bd3f022ad54a48ac6b",
        relative_path="icons/filled/player-pause.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/player-skip-back.svg",
        sha256="53c3b0f8e6c21ca64faac237065f721a3f2bfaaddcce6731958aa662e351a4c8",
        relative_path="icons/filled/player-skip-back.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/rotate.svg",
        sha256="27bd2a929eee1460da0948b24156194d65b0a33fb3ba356d6ee18572d4eb76b1",
        relative_path="icons/outline/rotate.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/chevron-left.svg",
        sha256="55836d95fec17c6eaafa376b1c203c79fd04baf32fe1cd528a6df6884e16e866",
        relative_path="icons/outline/chevron-left.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/chevron-right.svg",
        sha256="0142561fc2fd1b6a18b2cb0c08959b6037643aecf6adc6612aefe16ec4f39f0f",
        relative_path="icons/outline/chevron-right.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/alert-triangle.svg",
        sha256="92a951d8e90c1b1d986449e74a8b2e1c52982dc3dbe09b141ac11990420408f4",
        relative_path="icons/outline/alert-triangle.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/info-circle.svg",
        sha256="bf4377dff0aaaddf1676a4bb7292f90f1102f19daaca7021331ba6a191317c1c",
        relative_path="icons/outline/info-circle.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/check.svg",
        sha256="012d69548a769f4287e0af4c535b2cdf6e40457651de8078c4e588f0488952d6",
        relative_path="icons/outline/check.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/chevron-down.svg",
        sha256="a3a801faaeeb084e8a96897a5e2f355870b861e9606a1b50a89011b2718cc322",
        relative_path="icons/outline/chevron-down.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/x.svg",
        sha256="445e1b53563ad7e089f94dee923c2ef3444c63d45294a733687471a76a3c3b8d",
        relative_path="icons/outline/x.svg",
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
    """Unconditionally fetch and verify every given asset, overwriting
    whatever's already at its destination. Used for an explicit, deliberate
    refresh (`fledermap fetch-assets`) -- `ensure_vendor_assets` below is
    what runs automatically and only touches what's actually missing."""
    for asset in assets:
        with urllib.request.urlopen(asset.url) as response:
            data = response.read()
        verify(data, asset.sha256)
        dest = vendor_dir / asset.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def missing_assets(
    vendor_dir: Path,
    assets: tuple[VendorAsset, ...] = ASSETS,
) -> tuple[VendorAsset, ...]:
    return tuple(a for a in assets if not (vendor_dir / a.relative_path).exists())


def ensure_vendor_assets(
    vendor_dir: Path,
    assets: tuple[VendorAsset, ...] = ASSETS,
) -> tuple[VendorAsset, ...]:
    """Fetch only what's missing -- idempotent, so calling this on every
    `serve` startup costs nothing once the cache is warm. Returns the assets
    that were actually fetched, so a caller can report what happened rather
    than fetching silently."""
    to_fetch = missing_assets(vendor_dir, assets)
    fetch_all(vendor_dir, to_fetch)
    return to_fetch
