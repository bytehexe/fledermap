"""GET /rtc-widget -- the RTC sidebar widget's htmx-polled content (design spec
2026-09-26-fledermap-rtc-widget-design.md). A view, not an /api/* route: every other
htmx-consumed piece of content in this app is a rendered HTML fragment from web/views/, and
/api/* is exclusively GeoJSON for Leaflet -- a genuinely different consumer. See the design
spec's "Backend route" section for the correction this reflects."""

from __future__ import annotations

from datetime import UTC, datetime

import flask

from fledermap.services.sun import RtcSnapshot, compute_rtc

rtc_bp = flask.Blueprint("rtc", __name__, template_folder="../templates")

_LINE1 = {
    "day": "Daylight",
    "dawn": "Dawn",
    "dusk": "Dusk",
    "night": "Night",
    "always_day": "Polar day",
    "always_night": "Polar night",
}

_ICON_NAME = {
    "day": "sun",
    "dawn": "sunrise",
    "dusk": "sunset",
    "always_day": "sun",
}


def _render(snapshot: RtcSnapshot) -> str:
    if snapshot.extreme_latitude is not None:
        line1 = _LINE1[snapshot.extreme_latitude]
        if snapshot.extreme_latitude == "always_night":
            assert snapshot.moon is not None
            return flask.render_template(
                "_rtc_content.html",
                icon_name=None,
                moon_bucket=snapshot.moon.phase,
                line1=line1,
                line2=None,
            )
        return flask.render_template(
            "_rtc_content.html",
            icon_name=_ICON_NAME["always_day"],
            moon_bucket=None,
            line1=line1,
            line2=None,
        )

    assert snapshot.state is not None
    state = snapshot.state.value
    line1 = _LINE1[state]
    line2 = None
    if snapshot.next_transition is not None:
        label = snapshot.next_transition.label
        time_str = snapshot.next_transition.at.strftime("%H:%M")
        line2 = f"{label} {time_str}"

    if state == "night":
        assert snapshot.moon is not None
        return flask.render_template(
            "_rtc_content.html",
            icon_name=None,
            moon_bucket=snapshot.moon.phase,
            line1=line1,
            line2=line2,
        )
    return flask.render_template(
        "_rtc_content.html",
        icon_name=_ICON_NAME[state],
        moon_bucket=None,
        line1=line1,
        line2=line2,
    )


@rtc_bp.get("/rtc-widget")
def rtc_widget() -> flask.Response:
    if not flask.current_app.config["RTC_ENABLED"]:
        return flask.make_response(("", 404))

    location = flask.current_app.config["RTC_LOCATION"]
    if location is not None:
        lat, lon = location
    else:
        lat = flask.request.args.get("lat", type=float)
        lon = flask.request.args.get("lon", type=float)
        if lat is None or lon is None:
            return flask.make_response(("", 204))

    tz = flask.current_app.config["DISPLAY_TIMEZONE"]
    snapshot = compute_rtc(lat, lon, datetime.now(tz=UTC), tz)
    html = _render(snapshot)
    return flask.make_response(html)
