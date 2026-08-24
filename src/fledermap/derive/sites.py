"""Site clustering. DBSCAN partitions; `GeoCluster` (geo_cluster.py) describes
what it finds (spec section 7)."""

from __future__ import annotations

import numpy as np
from shapely.geometry import MultiPoint
from sklearn.cluster import DBSCAN

from fledermap.util.projection import LocalProjection


def cluster_points(
    lonlat: np.ndarray,
    *,
    eps_m: float,
    min_points: int,
) -> np.ndarray:
    """DBSCAN over `lonlat` (shape (n, 2), columns lon/lat); `eps_m` is metres.

    Projects through `LocalProjection` first so `eps_m` means metres, not
    degrees — parent spec section 7's pinned pitfall. Returns one integer
    cluster label per input row; -1 marks noise (a one-off spot, not an error).
    """
    if lonlat.shape[0] == 0:
        return np.array([], dtype=int)

    projection = LocalProjection(MultiPoint(lonlat.tolist()))
    local = projection.to_local_np(lonlat)
    return DBSCAN(eps=eps_m, min_samples=min_points).fit_predict(local)
