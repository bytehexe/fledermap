"""Species and Site list + detail pages -- full standalone pages (not HTMX
drawer fragments), same precedent as `sessions.py`/`recording_detail.py`.
Prerequisite for the statistics feature (docs/superpowers/specs/2026-09-05-
fledermap-statistics-design.md), which links into these detail pages.

Per CLAUDE.md's Drawer/detail-page feature-parity note: the site detail page
here deliberately calls `map_query.py`'s existing `site_detail()` (same
content as the map drawer's `_site_panel.html` today) through its own route
and template, rather than embedding the drawer partial -- so the two can
diverge later without being coupled."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.entities import list_sites, list_species, species_detail
from fledermap.services.map_query import site_detail
from fledermap.store.geo import decode_point
from fledermap.web.params import fallback_site_label

entities_bp = flask.Blueprint(
    "entities",
    __name__,
    template_folder="../templates",
)


@entities_bp.get("/species")
def species_list_page() -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        rows = list_species(session)
        html = flask.render_template("species_list.html", rows=rows)
    return flask.make_response(html)


@entities_bp.get("/species/<int:taxon_id>")
def species_detail_page(taxon_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        detail = species_detail(session, taxon_id)
        if detail is None:
            flask.abort(404)
        html = flask.render_template("species_detail.html", detail=detail)
    return flask.make_response(html)


@entities_bp.get("/sites")
def sites_list_page() -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        sites = list_sites(session)
        rows = [
            (
                site,
                site.name
                if site.name
                else fallback_site_label(decode_point(site.centroid)),
            )
            for site in sites
        ]
        html = flask.render_template("sites_list.html", rows=rows)
    return flask.make_response(html)


@entities_bp.get("/sites/<int:site_id>")
def site_detail_page(site_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        detail = site_detail(session, site_id)
        if detail is None:
            flask.abort(404)
        point = decode_point(detail.site.centroid)
        label = detail.site.name if detail.site.name else fallback_site_label(point)
        html = flask.render_template("site_detail.html", detail=detail, label=label)
    return flask.make_response(html)
