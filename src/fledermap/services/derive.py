"""Site derivation use-case layer. See spec section 7."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from geoalchemy2.elements import WKTElement
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.derive.geo_cluster import GeoCluster
from fledermap.derive.sites import cluster_points
from fledermap.domain.codes import Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site


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
    """Wholesale rebuild of `site` from GPS-bearing recordings with an
    identified-species current-best identification.

    A site is a place where we find bats -- not a session-scoped concept
    (design spec 2026-08-29-fledermap-identification-based-sites-design.md,
    decision SB-1): every GPS-bearing recording is eligible regardless of
    which session (or session kind) it belongs to, filtered to
    `Verdict.SPECIES` via `current_best_identification` -- the same rule
    `map_query._passes_verdict_filter` already applies by default to hide
    noise on the map. A recording with no non-superseded identification at
    all is excluded, matching that same rule's treatment of "no best" as
    equivalent to `NO_ID`.

    Idempotent — safe to re-run at any time (spec section 7: "tuning is
    free"). A recording left with `site_id = NULL` is a one-off spot or
    unidentified, not an error. An identification change is picked up
    automatically the next time this runs, with no separate invalidation
    needed (decision SB-3): `Identification` rows are only ever written by
    `services/ingest.py`'s `commit_scan`, which only ever runs inside
    `run_ingest_cycle` -- and that already calls this function
    unconditionally every cycle.

    `DELETE FROM site`, never `TRUNCATE`: Postgres `TRUNCATE` does not fire
    `ON DELETE SET NULL` the way `DELETE` does — it would either error on the
    referencing `recording.site_id` FK or, with CASCADE, truncate `recording`
    too.
    """
    candidates = db_session.scalars(
        select(Recording).where(Recording.geom.is_not(None)),
    )
    recordings = [
        r
        for r in candidates
        if (best := current_best_identification(r)) is not None
        and best.verdict == Verdict.SPECIES
    ]

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
