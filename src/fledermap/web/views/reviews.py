"""The Reviews page (docs/superpowers/specs/2026-09-09-fledermap-flagged-
for-review-design.md): the dedicated entry point into the flagged-for-review
workflow -- a count + "Start reviewing" link into the first flagged
recording's details page, plus a table to jump into any individual one
directly. Both links carry the SAME fixed review=<id-list> snapshot (see
services/review_flags.py's build_review_snapshot) -- computed once here,
not recomputed as the reviewer works through it."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.services.map_query import filtered_recordings
from fledermap.services.review_flags import (
    MAX_REVIEW_SNAPSHOT,
    ReviewContext,
    build_review_snapshot,
    review_reasons,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Site
from fledermap.web.params import fallback_site_label

reviews_bp = flask.Blueprint("reviews", __name__, template_folder="../templates")


@reviews_bp.get("/reviews")
def reviews_page() -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recordings = filtered_recordings(session, needs_review=True)
        context = ReviewContext.build(session)
        snapshot_ids = build_review_snapshot(recordings)
        review_qs = ",".join(str(i) for i in snapshot_ids)

        rows = []
        for r in recordings[:MAX_REVIEW_SNAPSHOT]:
            best = current_best_identification(r)
            species_label = (
                best.primary.taxon.scientific_name
                if best is not None and not best.is_multi and best.primary.taxon
                else "unmapped species"
            )
            site = session.get(Site, r.site_id) if r.site_id else None
            site_label = (
                site.name
                if site and site.name
                else fallback_site_label(decode_point(site.centroid))
                if site
                else None
            )
            reasons = review_reasons(r, context)
            if r.flagged_for_review:
                reasons = [*reasons, "manually flagged"]
            rows.append(
                {
                    "audio_hash": r.audio_hash,
                    "site_label": site_label,
                    "recorded_at": r.recorded_at,
                    "species_label": species_label,
                    "reasons": reasons,
                },
            )

        html = flask.render_template(
            "reviews.html",
            count=len(recordings),
            rows=rows,
            review_qs=review_qs,
            truncated=len(recordings) > MAX_REVIEW_SNAPSHOT,
        )
    return flask.make_response(html)
