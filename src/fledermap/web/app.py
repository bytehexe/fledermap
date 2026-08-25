"""Flask app factory (design spec section 3/4). `web/api` and `web/views`
both call `services/`, never `store/` directly -- the SPA-migration escape
hatch the parent spec's section 4 documents depends on that boundary holding.
"""

from __future__ import annotations

from pathlib import Path

import flask
from sqlalchemy import Engine

from fledermap.web.api.geojson import api_bp
from fledermap.web.views.map import views_bp


def create_app(engine: Engine, static_root: Path) -> flask.Flask:
    """`static_root` is `Config.static_root` -- where
    `scripts/fetch_vendor_assets.py` (Task 3) wrote Leaflet/HTMX/Alpine.
    Served from a dedicated `vendor` Blueprint (its own `static_folder`),
    kept separate from the app's own default static folder (which serves
    this package's own committed `app.js`/`app.css` -- Task 7) so the two
    genuinely different kinds of static content (fetched-at-setup-time vs.
    committed-with-the-code) never share one directory or one config knob.
    """
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine

    vendor_bp = flask.Blueprint(
        "vendor",
        __name__,
        static_folder=str(static_root / "vendor"),
        static_url_path="/static/vendor",
    )
    app.register_blueprint(vendor_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)
    return app
