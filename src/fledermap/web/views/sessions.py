"""Sessions list + detail pages (design spec
2026-08-27-fledermap-phase5b-sessions-design.md) -- full standalone pages,
not HTMX drawer fragments, matching the parent spec treating `/sessions` as
a first-class view distinct from the map's drawer."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.sessions import filtered_sessions, open_proposal_session_ids
from fledermap.web.params import parse_datetime

sessions_bp = flask.Blueprint(
    "sessions",
    __name__,
    template_folder="../templates",
)


@sessions_bp.get("/sessions")
def sessions_list_page() -> flask.Response:
    detector = flask.request.args.get("detector") or None
    from_raw = flask.request.args.get("from", "")
    to_raw = flask.request.args.get("to", "")
    try:
        date_from = parse_datetime(from_raw)
        date_to = parse_datetime(to_raw, end_of_day=True)
    except ValueError as exc:
        return flask.make_response((str(exc), 400))
    open_only = flask.request.args.get("open_proposals") == "1"

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        rows = filtered_sessions(
            session,
            detector=detector,
            date_from=date_from,
            date_to=date_to,
            open_proposals_only=open_only,
        )
        open_ids = open_proposal_session_ids(session)
        html = flask.render_template(
            "sessions_list.html",
            rows=rows,
            open_ids=open_ids,
            detector=detector or "",
            date_from=from_raw,
            date_to=to_raw,
            open_only=open_only,
        )
    return flask.make_response(html)
