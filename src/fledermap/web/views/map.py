"""The map page (design spec section 3/9)."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.map_query import list_sessions, list_taxa

views_bp = flask.Blueprint("views", __name__, template_folder="../templates")


@views_bp.get("/")
def map_page() -> str:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        return flask.render_template(
            "map.html",
            taxa=list_taxa(session),
            sessions=list_sessions(session),
        )
