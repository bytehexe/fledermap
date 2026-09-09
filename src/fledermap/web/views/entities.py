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

from fledermap.services.entities import (
    RECENT_RECORDINGS_LIMIT,
    list_sites,
    list_species,
    species_detail,
)
from fledermap.services.map_query import site_detail
from fledermap.store.geo import decode_point
from fledermap.web.params import fallback_site_label, is_safe_relative_path


def _resolve_back_link(
    return_to: str | None, default: tuple[str, str]
) -> tuple[str, str]:
    """Shared by the species/site detail pages -- see docs/style-guide.md's
    "Back-links (return_to)" section. A recognised map origin gets "Back to
    map"; anything else safe just gets "Back"; missing/unsafe falls back to
    the page's own default (its own list page, since that's where a reader
    lands with no more specific origin in hand)."""
    if return_to is None or not is_safe_relative_path(return_to):
        return default
    if return_to == "/" or return_to.startswith("/?"):
        return ("Back to map", return_to)
    return ("Back", return_to)


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
        back_label, back_url = _resolve_back_link(
            flask.request.args.get("return_to"),
            ("All species", "/species"),
        )
        html = flask.render_template(
            "species_detail.html",
            detail=detail,
            recent_recordings_limit=RECENT_RECORDINGS_LIMIT,
            back_label=back_label,
            back_url=back_url,
        )
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
        back_label, back_url = _resolve_back_link(
            flask.request.args.get("return_to"),
            ("All sites", "/sites"),
        )
        html = flask.render_template(
            "site_detail.html",
            detail=detail,
            label=label,
            back_label=back_label,
            back_url=back_url,
        )
    return flask.make_response(html)
