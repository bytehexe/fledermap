from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import MergeResolution, SessionKind
from fledermap.store.models import Recording, SessionMergeProposal
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_sessions_list_renders_a_session_row(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="EMT\x1f1",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "EMT" in html
    assert "stationary" in html


def test_sessions_list_filters_by_detector(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="EMT\x1f1",
            ),
        )
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="Kaleidoscope\x1f2",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?detector=EMT")

    html = response.get_data(as_text=True)
    assert "EMT" in html
    assert "Kaleidoscope" not in html


def test_sessions_list_empty_state(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    assert response.status_code == 200
    assert "No sessions match" in response.get_data(as_text=True)


def test_sessions_list_bad_date_returns_400(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?from=not-a-date")

    assert response.status_code == 400


def test_session_detail_not_found_returns_404(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions/999")

    assert response.status_code == 404


def test_session_detail_shows_edit_form_with_current_values(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            kind=SessionKind.TRANSECT,
            detector_key="EMT\x1f1",
            note="existing note",
            weather="rainy",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "existing note" in html
    assert "rainy" in html
    assert 'value="transect" selected' in html
    assert "effort" not in html.lower()  # P5b-10: field is gone


def test_session_detail_lists_recordings(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"),
                path="a.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=s.id,
            ),
        )
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    html = response.get_data(as_text=True)
    assert "Recordings in this session (1)" in html
    assert "unidentified" in html  # no Identification rows -- current_best is None


def test_session_detail_no_merge_banner_when_no_open_proposal(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    assert "merge-banner" not in response.get_data(as_text=True)


def test_save_session_updates_kind_note_weather_and_locks(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/{session_id}",
        data={"kind": "transect", "note": "new note", "weather": "clear"},
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{session_id}"
    with OrmSession(engine) as session:
        refreshed = session.get(AnnotationSession, session_id)
        assert refreshed is not None
        assert refreshed.kind == SessionKind.TRANSECT
        assert refreshed.note == "new note"
        assert refreshed.weather == "clear"
        assert refreshed.kind_locked is True


def test_save_session_invalid_kind_returns_400(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(f"/sessions/{session_id}", data={"kind": "bogus"})

    assert response.status_code == 400


def test_save_session_not_found_returns_404(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        "/sessions/999",
        data={"kind": "stationary", "note": "", "weather": ""},
    )

    assert response.status_code == 404


def _make_open_proposal(
    session: OrmSession,
) -> tuple[AnnotationSession, AnnotationSession, SessionMergeProposal]:
    a = AnnotationSession(
        started_at=datetime(2026, 8, 21, tzinfo=UTC),
        ended_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        kind=SessionKind.STATIONARY,
        detector_key="EMT\x1f1",
    )
    b = AnnotationSession(
        started_at=datetime(2026, 8, 22, tzinfo=UTC),
        ended_at=datetime(2026, 8, 22, tzinfo=UTC),
        kind=SessionKind.STATIONARY,
        detector_key="EMT\x1f1",
    )
    session.add_all([a, b])
    session.flush()
    session.add(
        Recording(
            audio_hash="a".rjust(64, "0"),
            path="a.wav",
            recorded_at=datetime(2026, 8, 21, tzinfo=UTC),
            session_id=a.id,
        ),
    )
    session.flush()
    bridging = session.scalars(
        select(Recording).where(Recording.session_id == a.id)
    ).one()
    proposal = SessionMergeProposal(
        session_a_id=a.id,
        session_b_id=b.id,
        bridging_recording_id=bridging.id,
        detected_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    session.add(proposal)
    session.commit()
    return a, b, proposal


def test_resolve_proposal_merge_redirects_to_session_a(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        a, _b, proposal = _make_open_proposal(session)
        a_id, proposal_id = a.id, proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "merge", "note": "combined", "weather": "combined"},
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{a_id}"
    with OrmSession(engine) as session:
        refreshed = session.get(SessionMergeProposal, proposal_id)
        assert refreshed is not None
        assert refreshed.resolution == MergeResolution.MERGED


def test_resolve_proposal_reject_redirects_to_session_a(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        a, _b, proposal = _make_open_proposal(session)
        a_id, proposal_id = a.id, proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "reject"},
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{a_id}"


def test_resolve_proposal_not_found_returns_404(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        "/sessions/merge-proposals/999/resolve",
        data={"action": "reject"},
    )

    assert response.status_code == 404


def test_resolve_proposal_already_resolved_returns_409(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        _a, _b, proposal = _make_open_proposal(session)
        proposal.resolution = MergeResolution.REJECTED
        proposal.resolved_at = datetime(2026, 8, 21, tzinfo=UTC)
        session.commit()
        proposal_id = proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "merge"},
    )

    assert response.status_code == 409


def test_resolve_proposal_invalid_action_returns_400(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        _a, _b, proposal = _make_open_proposal(session)
        proposal_id = proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "bogus"},
    )

    assert response.status_code == 400


def test_sessions_list_includes_the_sidebar(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    html = response.get_data(as_text=True)
    assert 'id="sidebar"' in html
    assert 'href="/"' in html  # link back to the map
