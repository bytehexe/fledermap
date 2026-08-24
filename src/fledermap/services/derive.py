"""Site derivation use-case layer. See spec section 7."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from geoalchemy2.elements import WKTElement
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import raiseload

from fledermap.derive.geo_cluster import GeoCluster
from fledermap.derive.sites import cluster_points
from fledermap.domain.codes import SessionKind
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Session, Site


@dataclass
class SiteDeriveReport:
    site_count: int = 0
    unclustered: int = 0


def derive_sites(
    db_session: OrmSession,
    *,
    eps_m: float,
    min_points: int,
) -> SiteDeriveReport:
    """Wholesale rebuild of `site` from stationary, GPS-bearing recordings.

    Idempotent — safe to re-run at any time (spec section 7: "tuning is free").
    A recording left with `site_id = NULL` is a one-off spot, not an error.

    `DELETE FROM site`, never `TRUNCATE`: Postgres `TRUNCATE` does not fire
    `ON DELETE SET NULL` the way `DELETE` does — it would either error on the
    referencing `recording.site_id` FK or, with CASCADE, truncate `recording`
    too.
    """
    recordings = list(
        db_session.scalars(
            select(Recording)
            .join(Session, Recording.session_id == Session.id)
            # `Recording.identifications` is `lazy="selectin"`; neither this
            # function nor anything it calls touches it, so eager-loading would
            # materialise the whole identification table on every run for
            # nothing. `raiseload` rather than `lazyload`: a future accidental
            # access should fail loudly, not silently become an N+1 query.
            .options(raiseload(Recording.identifications))
            .where(
                Session.kind == SessionKind.STATIONARY,
                Recording.geom.is_not(None),
            ),
        ),
    )

    db_session.execute(delete(Site))
    db_session.flush()

    report = SiteDeriveReport()
    if not recordings:
        return report

    points = np.array(
        [decode_point(r.geom) for r in recordings],
    )
    labels = cluster_points(points, eps_m=eps_m, min_points=min_points)

    by_label: dict[int, list[Recording]] = {}
    for recording, label in zip(recordings, labels, strict=True):
        if label == -1:
            report.unclustered += 1
            continue
        by_label.setdefault(int(label), []).append(recording)

    for members in by_label.values():
        locations: list[tuple[float, float]] = []
        for r in members:
            point = decode_point(r.geom)
            assert point is not None, "excluded by the geom IS NOT NULL query above"
            locations.append(point)
        # No z-score outlier removal here: DBSCAN's eps/min_points already
        # decided membership (noise is labelled -1 and excluded above), so
        # re-trimming is redundant at best. At worst it is wrong — `z < 1`
        # keeps only ~68% of a normal spread per axis, so `radius_m` would
        # describe roughly half the points while `recording_count` counts them
        # all. `GeoCluster`'s removal was built for mkmapdiary's different
        # problem: trimming GPS spikes out of a continuous track.
        cluster = GeoCluster(locations, remove_outliers=False)
        lon, lat = cluster.mass_point

        site = Site(
            centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
            # `cluster.radius` is `np.float64`: psycopg2 has no adapter for it,
            # so bound as a query parameter it renders as the literal text
            # `np.float64(...)` and Postgres reads `np` as a schema name.
            # Plain `float()` avoids the numpy scalar entirely.
            radius_m=float(cluster.radius),
            recording_count=len(members),
            first_at=min(r.recorded_at for r in members),
            last_at=max(r.recorded_at for r in members),
        )
        db_session.add(site)
        db_session.flush()
        for recording in members:
            recording.site_id = site.id
        report.site_count += 1

    return report
