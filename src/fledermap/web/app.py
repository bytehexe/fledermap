"""Flask app factory (design spec section 3/4). `web/api` and `web/views`
both call `services/`, never `store/` directly -- the SPA-migration escape
hatch the parent spec's section 4 documents depends on that boundary holding.

Also discovers the display timezone once at startup from Postgres's own
`current_setting('TimeZone')` (docs/superpowers/specs/
2026-09-14-fledermap-timezone-display-design.md) -- every `DateTime(timezone=True)`
column is already read back in that zone, so this names the existing
behavior rather than introducing a new one.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import flask
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import identification_label, recording_headline
from fledermap.web.api.geojson import api_bp
from fledermap.web.icons import make_icon_global
from fledermap.web.params import detector_label
from fledermap.web.timefmt import local_date, local_datetime
from fledermap.web.views.entities import entities_bp
from fledermap.web.views.map import views_bp
from fledermap.web.views.media import media_bp
from fledermap.web.views.recording_detail import recording_detail_bp
from fledermap.web.views.reviews import reviews_bp
from fledermap.web.views.sessions import sessions_bp
from fledermap.web.views.statistics import statistics_bp


def create_app(
    engine: Engine,
    static_root: Path,
    media_root: Path,
    archive_roots: tuple[Path, ...] = (),
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

    `archive_roots` is `Config.archive_roots` -- needed only by the
    recording-details page's detail-image routes (`web/views/media.py`) to
    resolve a recording's source WAV file directly; defaults to `()` so
    every other route, and every existing test that doesn't touch those
    two, is unaffected.
    """
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine
    app.config["MEDIA_ROOT"] = media_root
    app.config["ARCHIVE_ROOTS"] = archive_roots

    with OrmSession(engine) as session:
        display_timezone_name = session.execute(
            text("SELECT current_setting('TimeZone')")
        ).scalar_one()
    display_timezone = ZoneInfo(display_timezone_name)
    app.config["DISPLAY_TIMEZONE_NAME"] = display_timezone_name
    app.config["DISPLAY_TIMEZONE"] = display_timezone

    app.jinja_env.filters["detector_label"] = detector_label
    app.jinja_env.filters["local_datetime"] = lambda dt: local_datetime(
        dt, display_timezone
    )
    app.jinja_env.filters["local_date"] = lambda dt: local_date(dt, display_timezone)
    app.jinja_env.globals["recording_headline"] = recording_headline
    app.jinja_env.globals["identification_label"] = identification_label
    app.jinja_env.globals["icon"] = make_icon_global(static_root / "vendor")

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
    app.register_blueprint(recording_detail_bp)
    app.register_blueprint(entities_bp)
    app.register_blueprint(statistics_bp)
    app.register_blueprint(reviews_bp)
    return app
