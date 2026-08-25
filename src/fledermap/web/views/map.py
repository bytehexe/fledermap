"""The map page (design spec section 3/9)."""

from __future__ import annotations

import flask

views_bp = flask.Blueprint("views", __name__, template_folder="../templates")


@views_bp.get("/")
def map_page() -> str:
    return flask.render_template("map.html")
