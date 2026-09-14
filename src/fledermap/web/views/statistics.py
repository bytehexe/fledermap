"""Statistics dashboard pages (docs/superpowers/specs/2026-09-05-fledermap-
statistics-design.md). Full standalone pages, same precedent as
sessions.py/recording_detail.py/entities.py."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.map_query import site_detail
from fledermap.services.statistics import (
    rarest_species,
    rarest_unmapped_codes,
    recording_counts_by_hour,
    recording_counts_by_month,
    recording_counts_by_site,
    recording_counts_by_taxon,
    site_diversity,
    totals,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Taxon
from fledermap.web.params import fallback_site_label

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
            {
                "taxon": _taxon_json(e.taxon),
                "count": e.count,
                "statistics_url": f"/statistics/species/{e.taxon.id}",  # type: ignore[attr-defined]
            }
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
            # Every key must become a str, not just None -- Flask's
            # DefaultJSONProvider has sort_keys=True, and json.dumps(...,
            # sort_keys=True) raises TypeError comparing a str key ("null")
            # against an int key (a taxon id) the moment a series mixes a
            # real taxon breakdown with the Other/None bucket (caught live
            # as a real 500 on /statistics, 2026-09-08).
            {("null" if k is None else str(k)): v for k, v in bucket.items()}
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
        highest_estimated_richness_sites = site_diversity(
            session, sort_by="estimated_richness"
        )
        least_sampled_sites = site_diversity(session, sort_by="coverage")
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
            highest_estimated_richness_sites=highest_estimated_richness_sites.entries,
            least_sampled_sites=least_sampled_sites.entries,
            month=_series_json(month_series),
            hour=_series_json(hour_series),
            display_timezone_name=flask.current_app.config["DISPLAY_TIMEZONE_NAME"],
        )
    return flask.make_response(html)


def _site_breakdown_json(breakdown: object) -> dict[str, object]:
    return {
        "entries": [
            {
                "site": {"id": e.site.id, "name": e.site.name or f"Site #{e.site.id}"},
                "count": e.count,
                "statistics_url": f"/statistics/sites/{e.site.id}",  # type: ignore[attr-defined]
            }
            for e in breakdown.entries  # type: ignore[attr-defined]
        ],
    }


@statistics_bp.get("/statistics/species/<int:taxon_id>")
def species_statistics_page(taxon_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        taxon = session.get(Taxon, taxon_id)
        if taxon is None:
            flask.abort(404)
        species_totals = totals(session, taxon_id=taxon_id)
        site_breakdown = recording_counts_by_site(session, taxon_id=taxon_id)
        month_series = recording_counts_by_month(session, taxon_id=taxon_id)
        hour_series = recording_counts_by_hour(session, taxon_id=taxon_id)

        html = flask.render_template(
            "statistics_species.html",
            taxon=taxon,
            totals=species_totals,
            sites=_site_breakdown_json(site_breakdown),
            month=_series_json(month_series),
            hour=_series_json(hour_series),
            display_timezone_name=flask.current_app.config["DISPLAY_TIMEZONE_NAME"],
        )
    return flask.make_response(html)


@statistics_bp.get("/statistics/sites/<int:site_id>")
def site_statistics_page(site_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        site_info = site_detail(session, site_id)
        if site_info is None:
            flask.abort(404)
        label = (
            site_info.site.name
            if site_info.site.name
            else fallback_site_label(decode_point(site_info.site.centroid))
        )
        site_totals = totals(session, site_id=site_id)
        diversity = site_diversity(session, site_id=site_id).entries[0]
        donut = recording_counts_by_taxon(session, site_id=site_id)
        month_series = recording_counts_by_month(session, site_id=site_id)
        hour_series = recording_counts_by_hour(session, site_id=site_id)

        html = flask.render_template(
            "statistics_site.html",
            site=site_info.site,
            label=label,
            totals=site_totals,
            diversity=diversity,
            donut=_breakdown_json(donut),
            month=_series_json(month_series),
            hour=_series_json(hour_series),
            display_timezone_name=flask.current_app.config["DISPLAY_TIMEZONE_NAME"],
        )
    return flask.make_response(html)
