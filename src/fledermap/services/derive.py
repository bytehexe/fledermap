"""Site derivation use-case layer. See spec section 7."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from geoalchemy2.elements import WKTElement
from sqlalchemy import delete, select, update
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
    """Rebuild `site` from GPS-bearing recordings with an identified-species
    current-best identification, reconciled against the previous run so an
    unchanged site keeps its id (see "Rebuild reconciles..." below).

    A site is a place where we find bats -- not a session-scoped concept
    (design spec 2026-08-29-fledermap-identification-based-sites-design.md,
    decision SB-1): every GPS-bearing recording is eligible regardless of
    which session (or session kind) it belongs to, filtered to
    `Verdict.SPECIES` via `current_best_identification` -- the same rule
    `map_query._passes_verdict_filter` already applies by default to hide
    noise on the map. A recording with no non-superseded identification at
    all is excluded, matching that same rule's treatment of "no best" as
    equivalent to `NO_ID`. Unlike `map_query.py`'s recording queries, this
    one does not filter on `Recording.missing_since` -- a site is treated as
    a place, which doesn't stop existing just because one of its files went
    missing from the archive.

    Idempotent — safe to re-run at any time (spec section 7: "tuning is
    free"). A recording left with `site_id = NULL` is a one-off spot or
    unidentified, not an error. An identification change is picked up
    automatically the next time this runs, with no separate invalidation
    needed (decision SB-3): `Identification` rows are only ever written by
    `services/ingest.py`'s `commit_scan`, which only ever runs inside
    `run_ingest_cycle` -- and that already calls this function
    unconditionally every cycle.

    Rebuild reconciles against the previous run rather than deleting and
    recreating every `Site` unconditionally: a cluster whose membership
    overlaps an existing site keeps that site's row (and therefore its id
    and any poiidx-assigned name) — only a cluster with no matching previous
    site gets a fresh row, and only a previous site matched by nothing this
    run gets deleted. Without this, every cycle handed out fresh ids even
    when nothing about a site actually changed, and the worker now really
    does run every 5 minutes (plus at startup) — any client holding a site
    id across a cycle (an open drawer panel, a bookmarked map URL) would see
    it vanish within minutes. Matching is by recording-membership overlap,
    not centroid distance: a site's set of member recordings is the more
    stable signal (`GeoCluster`'s outlier-sensitive centroid can shift a bit
    between runs with no membership change at all).

    Never `TRUNCATE`: Postgres `TRUNCATE` does not fire `ON DELETE SET NULL`
    the way `DELETE` does — it would either error on the referencing
    `recording.site_id` FK or, with CASCADE, truncate `recording` too.
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

    # Capture the previous run's sites and their membership before touching
    # anything, so unchanged clusters below can be matched back to their old
    # site and reuse its id instead of getting a new one. `existing_site_ids`
    # is queried separately from `Site` itself, not inferred from which sites
    # still have members in `Recording` -- a site whose every member
    # `Recording` row was deleted outright (rather than just losing its
    # `site_id`) would otherwise vanish from `previous_members` entirely and
    # never be recognised as stale, leaking its row forever.
    existing_site_ids = set(db_session.scalars(select(Site.id)))
    previous_members: dict[int, set[int]] = {
        site_id: set() for site_id in existing_site_ids
    }
    for site_id, recording_id in db_session.execute(
        select(Recording.site_id, Recording.id).where(Recording.site_id.is_not(None)),
    ):
        previous_members.setdefault(site_id, set()).add(recording_id)

    db_session.execute(update(Recording).values(site_id=None))
    db_session.flush()

    report = SiteDeriveReport()
    if not recordings:
        db_session.execute(delete(Site))
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

    # Greedily match each new cluster to the previous site it overlaps most
    # with, largest overlap first, so a split/merge doesn't let a smaller
    # overlap claim a site before a bigger one gets the chance.
    def best_previous_overlap(members: list[Recording]) -> int:
        member_ids = {r.id for r in members}
        return max(
            (len(member_ids & ids) for ids in previous_members.values()), default=0
        )

    clusters_by_overlap = sorted(
        by_label.values(), key=best_previous_overlap, reverse=True
    )

    matched_site_ids: set[int] = set()
    clusters_with_reuse: list[tuple[list[Recording], int | None]] = []
    for members in clusters_by_overlap:
        member_ids = {r.id for r in members}
        best_site_id: int | None = None
        best_overlap = 0
        for site_id, ids in previous_members.items():
            if site_id in matched_site_ids:
                continue
            overlap = len(member_ids & ids)
            if overlap > best_overlap:
                best_overlap = overlap
                best_site_id = site_id
        if best_site_id is not None:
            matched_site_ids.add(best_site_id)
        clusters_with_reuse.append((members, best_site_id))

    for members, reuse_site_id in clusters_with_reuse:
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

        # `cluster.radius` is `np.float64`: psycopg2 has no adapter for it, so
        # bound as a query parameter it renders as the literal text
        # `np.float64(...)` and Postgres reads `np` as a schema name. Plain
        # `float()` avoids the numpy scalar entirely.
        radius_m = float(cluster.radius)
        first_at = min(r.recorded_at for r in members)
        last_at = max(r.recorded_at for r in members)

        if reuse_site_id is not None:
            site = db_session.get(Site, reuse_site_id)
            assert site is not None, "id came from a query moments ago"
            site.centroid = WKTElement(f"POINT({lon} {lat})", srid=4326)
            site.radius_m = radius_m
            site.recording_count = len(members)
            site.first_at = first_at
            site.last_at = last_at
            # `site.name`/`site.admin_path` deliberately untouched: reusing
            # the row keeps whatever poiidx already resolved for it, so an
            # unchanged site doesn't need renaming every cycle.
        else:
            site = Site(
                centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
                radius_m=radius_m,
                recording_count=len(members),
                first_at=first_at,
                last_at=last_at,
            )
            db_session.add(site)
        db_session.flush()
        for recording in members:
            recording.site_id = site.id
        report.site_count += 1

    stale_site_ids = existing_site_ids - matched_site_ids
    if stale_site_ids:
        db_session.execute(delete(Site).where(Site.id.in_(stale_site_ids)))

    return report
