"""Statistics dashboard pages (docs/superpowers/specs/2026-09-05-fledermap-
statistics-design.md). Full standalone pages, same precedent as
sessions.py/recording_detail.py/entities.py."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.statistics import (
    rarest_species,
    rarest_unmapped_codes,
    recording_counts_by_hour,
    recording_counts_by_month,
    recording_counts_by_taxon,
    site_diversity,
    totals,
)

statistics_bp = flask.Blueprint(
    "statistics",
    __name__,
    template_folder="../templates",
)


def _taxon_json(taxon: object) -> dict[str, object]:
    return {"id": taxon.id, "scientific_name": taxon.scientific_name}  # type: ignore[attr-defined]


def _breakdown_json(breakdown: object) -> dict[str, object]:
    return {
        "entries": [
            {"taxon": _taxon_json(e.taxon), "count": e.count}
            for e in breakdown.entries  # type: ignore[attr-defined]
        ],
        "other_count": breakdown.other_count,  # type: ignore[attr-defined]
        "unmapped_count": breakdown.unmapped_count,  # type: ignore[attr-defined]
        "multi_species_count": breakdown.multi_species_count,  # type: ignore[attr-defined]
    }


def _series_json(series: object) -> dict[str, object]:
    return {
        "labels": list(series.labels),  # type: ignore[attr-defined]
        "taxa": [_taxon_json(t) for t in series.taxa],  # type: ignore[attr-defined]
        "other_included": series.other_included,  # type: ignore[attr-defined]
        "single_species": series.single_species,  # type: ignore[attr-defined]
        "buckets": [
            {("null" if k is None else k): v for k, v in bucket.items()}
            for bucket in series.buckets  # type: ignore[attr-defined]
        ],
    }


@statistics_bp.get("/statistics")
def global_statistics_page() -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        global_totals = totals(session)
        donut = recording_counts_by_taxon(session)
        rarest = rarest_species(session)
        rarest_codes = rarest_unmapped_codes(session)
        richest_sites = site_diversity(session, sort_by="richness")
        diverse_sites = site_diversity(session, sort_by="shannon")
        month_series = recording_counts_by_month(session)
        hour_series = recording_counts_by_hour(session)

        html = flask.render_template(
            "statistics_global.html",
            totals=global_totals,
            donut=_breakdown_json(donut),
            rarest=rarest,
            rarest_codes=rarest_codes,
            richest_sites=richest_sites.entries,
            diverse_sites=diverse_sites.entries,
            month=_series_json(month_series),
            hour=_series_json(hour_series),
        )
    return flask.make_response(html)
