"""Sessions list + detail pages (design spec
2026-08-27-fledermap-phase5b-sessions-design.md) -- full standalone pages,
not HTMX drawer fragments, matching the parent spec treating `/sessions` as
a first-class view distinct from the map's drawer."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import SessionKind
from fledermap.services.current_best import current_best_identification
from fledermap.services.sessions import (
    filtered_sessions,
    open_proposal_session_ids,
    session_detail,
)
from fledermap.store.models import Session as AnnotationSession
from fledermap.store.models import Taxon
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


@sessions_bp.get("/sessions/<int:session_id>")
def session_detail_page(session_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        detail = session_detail(session, session_id)
        if detail is None:
            return flask.make_response(("Session not found.", 404))

        recordings_with_id = []
        for recording in detail.recordings:
            best = current_best_identification(recording)
            taxon = None
            if best is not None and best.taxon_id is not None:
                taxon = session.get(Taxon, best.taxon_id)
            recordings_with_id.append((recording, best, taxon))

        html = flask.render_template(
            "session_detail.html",
            detail=detail,
            recordings_with_id=recordings_with_id,
        )
    return flask.make_response(html)


@sessions_bp.post("/sessions/<int:session_id>")
def save_session(session_id: int) -> flask.Response:
    kind_raw = flask.request.form.get("kind", "")
    try:
        kind = SessionKind(kind_raw)
    except ValueError:
        return flask.make_response((f"Invalid kind: {kind_raw!r}", 400))
    note = flask.request.form.get("note") or None
    weather = flask.request.form.get("weather") or None

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        session_obj = session.get(AnnotationSession, session_id)
        if session_obj is None:
            return flask.make_response(("Session not found.", 404))
        session_obj.kind = kind
        session_obj.note = note
        session_obj.weather = weather
        session_obj.kind_locked = True
        session.commit()

    return flask.make_response(flask.redirect(f"/sessions/{session_id}"))
