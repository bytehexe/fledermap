from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, MergeResolution, Verdict, VisualSighting
from fledermap.store.models import (
    Identification,
    Recording,
    SessionMergeProposal,
    Taxon,
)
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.app import create_app
from fledermap.web.timefmt import local_datetime

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def test_sessions_list_renders_a_session_row(engine: Engine, tmp_path: Path) -> None:
    started_at = datetime(2026, 8, 21, 21, tzinfo=UTC)
    ended_at = datetime(2026, 8, 21, 23, tzinfo=UTC)
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=started_at,
                ended_at=ended_at,
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
    assert "kind" not in html.lower()  # 2026-08-29: SessionKind removed entirely
    display_tz = app.config["DISPLAY_TIMEZONE"]
    expected_range = f"{local_datetime(started_at, display_tz)} – {local_datetime(ended_at, display_tz)}"
    assert expected_range in html


def test_sessions_list_filters_by_detector(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                detector_key="EMT\x1f1",
            ),
        )
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                detector_key="Kaleidoscope\x1f2",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?detector=EMT")

    html = response.get_data(as_text=True)
    # The detector *dropdown* legitimately lists every detector regardless
    # of the current filter (so a person can switch to a different one) --
    # only the *table* of matching rows should actually be narrowed.
    table_html = html.split('<table id="sessions-table">')[1].split("</table>")[0]
    assert "EMT" in table_html
    assert "Kaleidoscope" not in table_html


def test_sessions_list_detector_dropdown_pre_selects_the_current_filter(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                detector_key="EMT\x1f1",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?detector=EMT%1f1")

    html = response.get_data(as_text=True)
    assert 'value="EMT\x1f1" selected' in html


def test_sessions_list_shows_open_proposal_count(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        a = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        b = AnnotationSession(
            started_at=datetime(2026, 8, 22, tzinfo=UTC),
            ended_at=datetime(2026, 8, 22, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"),
                path="x.wav",
                recorded_at=datetime(2026, 8, 21, tzinfo=UTC),
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.scalars(select(Recording)).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging.id,
                detected_at=datetime(2026, 8, 21, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    html = response.get_data(as_text=True)
    assert "Open merge proposals only (1)" in html


def test_sessions_list_shows_zero_count_when_no_open_proposals(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The count is always shown, including zero -- a hidden-when-zero
    count previously read as 'is this broken?' rather than 'there are
    currently none' (feedback on the first live pass)."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    html = response.get_data(as_text=True)
    assert "Open merge proposals only (0)" in html


def test_sessions_list_form_has_live_filter_and_url_sync_attributes(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    html = response.get_data(as_text=True)
    assert 'hx-trigger="change"' in html
    assert 'hx-push-url="true"' in html
    assert 'hx-target="#sessions-table-wrapper"' in html
    assert 'hx-select="#sessions-table-wrapper"' in html


def test_sessions_list_has_no_filter_button(engine: Engine, tmp_path: Path) -> None:
    """Live filtering (the form above) makes an explicit submit button
    redundant -- removed on feedback from the first live pass."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    html = response.get_data(as_text=True)
    assert "Filter</button>" not in html


def test_sessions_list_empty_state(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    assert response.status_code == 200
    assert "No sessions match" in response.get_data(as_text=True)


def test_sessions_list_pagination_buttons_disabled_at_start(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                detector_key="EMT\x1f1",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")
    html = response.get_data(as_text=True)

    assert '<div class="pagination" id="sessions-pagination">' in html
    # At offset 0 there's no earlier page -- "Newer" is disabled
    # regardless of how much data exists (has_more, which gates "Older",
    # is covered separately by the offset test below).
    assert re.search(r"disabled[^>]*>.*?chevron-left.*?Newer</button>", html, re.DOTALL)


def test_sessions_list_offset_shows_the_next_page(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import fledermap.services.sessions as sessions_module

    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 20, tzinfo=UTC),
                ended_at=datetime(2026, 8, 20, tzinfo=UTC),
                detector_key="OLDER\x1f1",
            ),
        )
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 22, tzinfo=UTC),
                ended_at=datetime(2026, 8, 22, tzinfo=UTC),
                detector_key="NEWER\x1f1",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    first_page = client.get("/sessions").get_data(as_text=True)
    second_page = client.get("/sessions?offset=1").get_data(as_text=True)

    # Not "NEWER"/"OLDER" -- both detector labels also appear in the filter
    # form's <select> regardless of pagination (it lists every known
    # detector, not just what's on the current page). The row dates are
    # unique to each session's actual table row.
    assert "2026-08-22" in first_page
    assert "2026-08-20" not in first_page
    assert "2026-08-20" in second_page
    assert "2026-08-22" not in second_page


def test_sessions_list_negative_offset_clamps_to_zero(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?offset=-5")

    assert response.status_code == 200


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
            detector_key="EMT\x1f1",
            note="existing note",
            weather="rainy",
            seen_visually=VisualSighting.YES,
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
    assert 'value="yes" selected' in html
    assert "effort" not in html.lower()  # P5b-10: field is gone
    assert "kind" not in html.lower()  # 2026-08-29: SessionKind removed entirely


def test_session_detail_save_button_is_the_forms_primary_action(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Style-guide audit (2026-09-09): the session-edit form's Save button was the
    only submit button in the whole app missing `.button-primary`."""
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sessions/{session_id}").get_data(as_text=True)

    assert '<button type="submit" class="button-primary">Save</button>' in html


def test_session_detail_warns_before_leaving_unsaved_changes(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Style-guide audit (2026-09-09): neither of this page's two forms had a
    `beforeunload` guard at all, despite the Obsidian backlog's "All pages with a
    save button: Warn if you leave the page without saving!" item being checked
    off -- this page slipped through it. Guarded via unsaved_changes_guard.js
    (a real HTML form's own submit already navigates, so DOM presence -- not the
    guard's actual JS behavior, which needs a browser -- is what a Python test
    can verify)."""
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sessions/{session_id}").get_data(as_text=True)

    assert 'id="session-edit-form"' in html
    assert "unsaved_changes_guard.js" in html


def test_session_detail_links_the_sites_its_recordings_belong_to(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Style-guide interlinking principle: a site already lists its sessions
    (_site_panel.html/site_detail.html); this is the reverse direction."""
    from geoalchemy2.elements import WKTElement

    from fledermap.store.models import Site

    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 21, tzinfo=UTC),
            last_at=datetime(2026, 8, 21, tzinfo=UTC),
            name="Old Barn",
        )
        session.add(site)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"),
                path="a.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=s.id,
                site_id=site.id,
            ),
        )
        session.commit()
        session_id = s.id
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sessions/{session_id}").get_data(as_text=True)

    assert f'<a href="/sites/{site_id}">Old Barn</a>' in html


def test_session_detail_no_sites_line_when_no_recording_has_a_site(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sessions/{session_id}").get_data(as_text=True)

    assert "Sites:" not in html


def test_session_detail_lists_recordings(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
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


def test_session_detail_recording_rows_link_to_the_recording_details_page(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.flush()
        audio_hash = "a".rjust(64, "0")
        session.add(
            Recording(
                audio_hash=audio_hash,
                path="a.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=s.id,
            ),
        )
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/sessions/{session_id}")

    html = response.get_data(as_text=True)
    assert f'href="/recordings/{audio_hash}' in html


def test_session_detail_recording_rows_link_their_species(
    engine: Engine, tmp_path: Path
) -> None:
    """docs/style-guide.md's "an entity's name always links to its own page"
    rule -- confirmed missing here 2026-09-10 (session_detail.html rendered
    the species via recording_headline() with no <a> wrapper even though
    taxon was already in scope in the loop)."""
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="a".rjust(64, "0"),
            path="a.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            session_id=s.id,
        )
        recording.identifications = [
            Identification(
                source=IdSource.EMT_WAMD,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
        ]
        session.add(recording)
        session.commit()
        session_id = s.id
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/sessions/{session_id}")

    html = response.get_data(as_text=True)
    assert f'href="/species/{taxon_id}"' in html


def test_session_detail_no_merge_banner_when_no_open_proposal(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    assert "merge-banner" not in response.get_data(as_text=True)


def test_save_session_updates_note_weather_and_seen_visually(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/{session_id}",
        data={
            "note": "new note",
            "weather": "clear",
            "seen_visually": "yes",
        },
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{session_id}?saved=1"
    with OrmSession(engine) as session:
        refreshed = session.get(AnnotationSession, session_id)
        assert refreshed is not None
        assert refreshed.note == "new note"
        assert refreshed.weather == "clear"
        assert refreshed.seen_visually == VisualSighting.YES


def test_session_detail_page_shows_a_save_confirmation_after_saving(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The Save form is a plain full-page POST+redirect (no htmx swap to
    piggyback feedback on), so the `?saved=1` redirect target is the only
    signal the page has that the save actually happened -- without it, a
    successful save looks identical to the page just having loaded."""
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    plain_html = client.get(f"/sessions/{session_id}").get_data(as_text=True)
    assert "Saved" not in plain_html

    saved_html = client.get(f"/sessions/{session_id}?saved=1").get_data(as_text=True)
    assert "icons-tabler-outline icon-tabler-check" in saved_html
    assert "Saved" in saved_html


def test_save_session_invalid_seen_visually_returns_400(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/{session_id}",
        data={"seen_visually": "bogus"},
    )

    assert response.status_code == 400


def test_save_session_not_found_returns_404(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        "/sessions/999",
        data={
            "note": "",
            "weather": "",
            "seen_visually": "unclear",
        },
    )

    assert response.status_code == 404


def _make_open_proposal(
    session: OrmSession,
) -> tuple[AnnotationSession, AnnotationSession, SessionMergeProposal]:
    a = AnnotationSession(
        started_at=datetime(2026, 8, 21, tzinfo=UTC),
        ended_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        detector_key="EMT\x1f1",
    )
    b = AnnotationSession(
        started_at=datetime(2026, 8, 22, tzinfo=UTC),
        ended_at=datetime(2026, 8, 22, tzinfo=UTC),
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


def test_merge_banner_note_textarea_omits_separator_when_only_one_side_has_text(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Fix 3: pre-filling the merge banner's Combined note/weather boxes must
    not write a literal "---" when only one side actually has text -- a user
    accepting the merge without editing the box would otherwise persist that
    literal separator as the surviving session's note."""
    with OrmSession(engine) as session:
        a = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            detector_key="EMT\x1f1",
            note="only a has a note",
        )
        b = AnnotationSession(
            started_at=datetime(2026, 8, 22, tzinfo=UTC),
            ended_at=datetime(2026, 8, 22, tzinfo=UTC),
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
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging.id,
                detected_at=datetime(2026, 8, 21, tzinfo=UTC),
            ),
        )
        session.commit()
        session_id = a.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    html = response.get_data(as_text=True)
    banner_start = html.index('class="merge-banner"')
    start = html.index('<textarea name="note">', banner_start)
    end = html.index("</textarea>", start)
    note_textarea = html[start:end]
    assert "only a has a note" in note_textarea
    assert "---" not in note_textarea


def test_merge_banner_accept_button_is_confirm_gated(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """'Accept merge' permanently deletes the counterpart session
    (resolve_merge_proposal) with no undo -- style guide's "no single click
    may overwrite or delete stored data" rule requires a window.confirm()
    gate. 'Reject' only flips a resolution flag, so it must NOT be gated."""
    with OrmSession(engine) as session:
        a, _b, _proposal = _make_open_proposal(session)
        session_id = a.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    html = response.get_data(as_text=True)
    banner_start = html.index('class="merge-banner"')
    accept_start = html.index('value="merge"', banner_start)
    accept_end = html.index("</button>", accept_start)
    accept_button = html[accept_start:accept_end]
    assert "onclick=" in accept_button
    assert "window.confirm(" in accept_button

    reject_start = html.index('value="reject"', banner_start)
    reject_end = html.index("</button>", reject_start)
    reject_button = html[reject_start:reject_end]
    assert "onclick=" not in reject_button


def test_merge_badge_in_sessions_list_links_to_session_detail(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        a, _b, _proposal = _make_open_proposal(session)
        a_id = a.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    html = response.get_data(as_text=True)
    assert f'<a class="merge-badge" href="/sessions/{a_id}">' in html


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
