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
from fledermap.web.views.media import media_bp
from fledermap.web.views.sessions import sessions_bp


def create_app(
    engine: Engine,
    static_root: Path,
    media_root: Path,
    transect_distance_m: float = 150.0,
) -> flask.Flask:
    """`static_root` is `Config.static_root` -- where
    `services/vendor_assets.py`'s `ensure_vendor_assets` fetches Leaflet/HTMX/Alpine.
    Served from a dedicated `vendor` Blueprint (its own `static_folder`),
    kept separate from the app's own default static folder (which serves
    this package's own committed `app.js`/`app.css` -- Task 7) so the two
    genuinely different kinds of static content (fetched-at-setup-time vs.
    committed-with-the-code) never share one directory or one config knob.

    `media_root` is `Config.media_root` -- where `jobs/tasks.py` writes
    derived spectrograms and previews, served by the `media` Blueprint (see
    `web/views/media.py`).

    `transect_distance_m` is `Config.transect_distance_m`, used by the
    `/sessions/merge-proposals/{id}/resolve` route (`web/views/sessions.py`)
    when a merge reclassifies the surviving session's kind. Keyword-with-
    default rather than a new required positional -- every other caller of
    `create_app` has no reason to know about this value.
    """
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine
    app.config["MEDIA_ROOT"] = media_root
    app.config["TRANSECT_DISTANCE_M"] = transect_distance_m

    vendor_bp = flask.Blueprint(
        "vendor",
        __name__,
        static_folder=str(static_root / "vendor"),
        static_url_path="/static/vendor",
    )
    app.register_blueprint(vendor_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(sessions_bp)
    return app
