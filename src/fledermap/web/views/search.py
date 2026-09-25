"""The /search page (design spec docs/superpowers/specs/2026-09-25-fledermap-search-design.md) --
a full standalone page, not a drawer fragment, matching sessions.py's/entities.py's own precedent
for a first-class list view."""

from __future__ import annotations

import os

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.services.search import (
    MAX_SEARCH_RESULTS,
    parse_search_query,
    search_recordings,
)
from fledermap.store.models import Taxon
from fledermap.web.params import parse_int

search_bp = flask.Blueprint(
    "search",
    __name__,
    template_folder="../templates",
)


@search_bp.get("/search")
def search_page() -> flask.Response:
    q = flask.request.args.get("q", "")
    try:
        offset = max(0, parse_int(flask.request.args.get("offset")) or 0)
    except ValueError:
        return flask.make_response(("Invalid offset.", 400))

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        parsed = parse_search_query(q)
        page = search_recordings(session, parsed, offset=offset)

        rows = []
        for recording in page.rows:
            best = current_best_identification(recording)
            taxon = None
            if (
                best is not None
                and not best.is_multi
                and best.primary.taxon_id is not None
            ):
                taxon = session.get(Taxon, best.primary.taxon_id)
            rows.append(
                {
                    "recording": recording,
                    "filename": os.path.basename(recording.path),
                    "best": best,
                    "taxon": taxon,
                },
            )

        html = flask.render_template(
            "search.html",
            q=q,
            rows=rows,
            warnings=page.warnings,
            has_more=page.has_more,
            offset=offset,
            prev_offset=max(0, offset - MAX_SEARCH_RESULTS),
            next_offset=offset + MAX_SEARCH_RESULTS,
        )
    return flask.make_response(html)
