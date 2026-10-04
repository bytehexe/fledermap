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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import flask
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import identification_label, recording_headline
from fledermap.web.api.geojson import api_bp
from fledermap.web.icons import make_icon_global, make_moon_phase_icon_global
from fledermap.web.params import detector_label
from fledermap.web.timefmt import local_date, local_datetime, local_datetime_seconds
from fledermap.web.views.entities import entities_bp
from fledermap.web.views.map import views_bp
from fledermap.web.views.media import media_bp
from fledermap.web.views.recording_detail import recording_detail_bp
from fledermap.web.views.reviews import reviews_bp
from fledermap.web.views.rtc import rtc_bp
from fledermap.web.views.search import search_bp
from fledermap.web.views.sessions import sessions_bp
from fledermap.web.views.statistics import statistics_bp


def _resolve_display_timezone(display_timezone_name: str) -> ZoneInfo:
    """Wraps `ZoneInfo(...)` with an actionable error message -- pulled out of
    `create_app` so this string-to-ZoneInfo logic (and its failure mode) can
    be unit-tested directly against a bogus name, without needing a real
    Postgres connection to produce one."""
    try:
        return ZoneInfo(display_timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = (
            f"Postgres's configured timezone {display_timezone_name!r} "
            "(from SELECT current_setting('TimeZone')) is not a name Python's "
            "zoneinfo module recognizes. Check the DB server's `timezone` "
            "setting, and that the `tzdata` package is installed if running "
            "on a minimal/Alpine-based image."
        )
        raise RuntimeError(msg) from exc


def create_app(
    engine: Engine,
    static_root: Path,
    media_root: Path,
    archive_roots: tuple[Path, ...] = (),
    batdetect2_enabled: bool = False,
    battybirdnet_enabled: bool = False,
    combine_emt_display: bool = True,
    batdetect2_threshold: float = 0.8,
    battybirdnet_threshold: float = 0.6,
    batdetect2_annotation_display_threshold: float = 0.20,
    battybirdnet_annotation_display_threshold: float = 0.20,
    noise_snr_threshold_db: float = 2.0,
    rtc_enabled: bool = True,
    latitude: float | None = None,
    longitude: float | None = None,
    regions_enabled: bool = False,
    iucn_ranges_enabled: bool = False,
    iucn_range_edge_km: float = 100.0,
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

    `batdetect2_enabled` is `Config.batdetect2_enabled` -- the map page's
    Source filter only lists "batdetect2" as an option when this is true,
    since disabling the classifier purges every one of its claims (see
    `services/classifier_detections.py`'s `purge_source`), leaving nothing for the
    filter to match; defaults to `False`, same reasoning as `archive_roots`
    above. `battybirdnet_enabled` is `Config.battybirdnet_enabled` and gates
    the "battybirdnet" filter option the same way.

    `combine_emt_display` is `Config.combine_emt_display` -- when true (the default), the
    map's Source filter shows one combined "EMT" option instead of separate emt.guano/
    emt.wamd/emt.filename options, and the "Identifications" breakdown box folds those same
    three sources into one `"emt"` row (`services/current_best.py`'s `display_identifications`).

    `batdetect2_threshold`/`battybirdnet_threshold` are `Config.batdetect2_threshold`/
    `Config.battybirdnet_threshold` -- exposed on `app.config["CLASSIFIER_THRESHOLDS"]` so the
    recording-detail page can draw a batdetect2/battybirdnet annotation box's border dashed when
    its confidence falls below the operator's configured species-confidence threshold (design spec
    2026-09-24-fledermap-batdetect2-annotation-layer-design.md §4). Defaults match `Config`'s own
    defaults, same reasoning as `combine_emt_display`'s default above.

    `batdetect2_annotation_display_threshold`/`battybirdnet_annotation_display_threshold` are
    `Config.batdetect2_annotation_display_threshold`/
    `Config.battybirdnet_annotation_display_threshold` -- exposed on
    `app.config["CLASSIFIER_DISPLAY_THRESHOLDS"]`, gating whether an annotation box is rendered
    at all (below the fixed, non-configurable storage floor everything already clears just to be
    cached -- see `jobs/tasks.py`'s `_BATDETECT2_FLOOR`/`_BATTYBIRDNET_FLOOR` -- but independent
    of `batdetect2_threshold`/`battybirdnet_threshold` above, which only affect claim
    materialization/box styling, not visibility).

    `noise_snr_threshold_db` is `Config.noise_snr_threshold_db` -- exposed on
    `app.config["NOISE_SNR_THRESHOLD_DB"]` so the recording-details page's Classify box can
    suggest "Noise" when a recording's own `snr_db` falls below it, using the same comparison
    `services/noise.py` makes for `fledermap.noise`'s own claim (design spec
    2026-09-25-fledermap-classify-suggestions-design.md). Default matches `Config`'s own
    default.

    `regions_enabled` is `Config.poiidx_database_url is not None` -- whether the map shows the
    Region filter. The web process never talks to poiidx itself, it only reads the regions the
    worker stored; this flag is what keeps rows left over from a past poiidx-enabled run from
    showing once poiidx is switched off.

    `iucn_ranges_enabled`/`iucn_range_edge_km` are `Config.iucn_ranges_enabled`/
    `Config.iucn_range_edge_km` -- whether the Identifications matrix gets its `iucn` range
    column and where "near" turns into "far". Like regions, the web process only reads what the
    worker cached; ranges left over from a past enabled run stay hidden once it is switched off.
    """
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine
    app.config["MEDIA_ROOT"] = media_root
    app.config["ARCHIVE_ROOTS"] = archive_roots
    app.config["BATDETECT2_ENABLED"] = batdetect2_enabled
    app.config["BATTYBIRDNET_ENABLED"] = battybirdnet_enabled
    app.config["COMBINE_EMT_DISPLAY"] = combine_emt_display
    app.config["REGIONS_ENABLED"] = regions_enabled
    app.config["IUCN_RANGES_ENABLED"] = iucn_ranges_enabled
    app.config["IUCN_RANGE_EDGE_KM"] = iucn_range_edge_km
    app.config["CLASSIFIER_THRESHOLDS"] = {
        "batdetect2": batdetect2_threshold,
        "battybirdnet": battybirdnet_threshold,
    }
    app.config["CLASSIFIER_DISPLAY_THRESHOLDS"] = {
        "batdetect2": batdetect2_annotation_display_threshold,
        "battybirdnet": battybirdnet_annotation_display_threshold,
    }
    app.config["NOISE_SNR_THRESHOLD_DB"] = noise_snr_threshold_db
    app.config["RTC_ENABLED"] = rtc_enabled
    app.config["RTC_LOCATION"] = (
        (latitude, longitude)
        if latitude is not None and longitude is not None
        else None
    )

    with OrmSession(engine) as session:
        display_timezone_name: str = session.execute(
            text("SELECT current_setting('TimeZone')")
        ).scalar_one()
    display_timezone = _resolve_display_timezone(display_timezone_name)
    app.config["DISPLAY_TIMEZONE_NAME"] = display_timezone_name
    app.config["DISPLAY_TIMEZONE"] = display_timezone

    app.jinja_env.filters["detector_label"] = detector_label
    app.jinja_env.filters["local_datetime"] = lambda dt: local_datetime(
        dt, display_timezone
    )
    app.jinja_env.filters["local_date"] = lambda dt: local_date(dt, display_timezone)
    app.jinja_env.filters["local_datetime_seconds"] = lambda dt: local_datetime_seconds(
        dt, display_timezone
    )
    app.jinja_env.globals["recording_headline"] = recording_headline
    app.jinja_env.globals["identification_label"] = identification_label
    app.jinja_env.globals["icon"] = make_icon_global(static_root / "vendor")
    app.jinja_env.globals["display_timezone_name"] = display_timezone_name
    app.jinja_env.globals["moon_phase_icon"] = make_moon_phase_icon_global(
        Path(__file__).parent / "static" / "icons"
    )
    app.jinja_env.globals["rtc_enabled"] = rtc_enabled

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
    app.register_blueprint(search_bp)
    app.register_blueprint(rtc_bp)
    return app
