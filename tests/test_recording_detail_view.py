from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_recording_details_page_404s_for_an_unknown_hash(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f1' * 32}")

    assert response.status_code == 404


def test_recording_details_page_renders_the_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                make="Wildlife Acoustics",
                model="Echo Meter Touch 2",
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f2' * 32}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Echo Meter Touch 2" in html
    assert f"/recordings/{'f2' * 32}/detail-spectrogram/0.webp" in html
    assert f"/recordings/{'f2' * 32}/detail-oscillogram/0.webp" in html


def test_recording_details_page_explains_a_missing_site_as_an_outlier(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A recording with no `site_id` (outside every derived site's radius,
    or geo-less) must say so explicitly rather than just omitting the
    "Site: ..." line -- otherwise a reader can't tell "outlier" apart from
    "the site info silently failed to load"."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f3' * 32}")

    html = response.get_data(as_text=True)
    assert "No site (outlier)" in html


def test_recording_details_page_site_link_points_at_the_sites_own_page(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """docs/style-guide.md's ".entity-header" rule: an entity's own name is
    always the link to its own dedicated page -- not the map filtered to it
    (`/?site=...`, this page's own previous behaviour)."""
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
            name="Old Barn",
        )
        session.add(site)
        session.flush()
        session.add(
            Recording(
                audio_hash="f4" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                site_id=site.id,
            ),
        )
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/recordings/{'f4' * 32}").get_data(as_text=True)

    assert f'href="/sites/{site_id}?return_to=/recordings/{"f4" * 32}"' in html
    assert "/?site=" not in html


def test_recording_details_page_bakes_in_the_preview_time_expansion_factor(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The <audio> element plays the same x10 time-expanded preview
    `media/preview.py` renders everywhere else -- `audio.currentTime` is on
    that expanded timeline, not the spectrogram's native-real-time locked
    scale, so the JS needs this factor to convert between them (cursor sync
    and click-to-play were both wrong without it: the cursor crawled at 1/10
    speed and scrolled off the image with ~90% of playback still left)."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f5" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f5' * 32}")

    html = response.get_data(as_text=True)
    assert 'data-time-expansion-factor="10"' in html


def test_recording_details_page_reserves_the_final_wrap_sizes_up_front(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Both wraps get their real final width/height inline, from the same server-known
    numbers the tiles themselves use, instead of collapsing to whatever their (all-`hidden`)
    children happen to take up -- avoids a layout jump once tiles reveal, and means the
    scroll container's horizontal range is correct from the very first paint."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f7" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,  # width_px = round(500 * 4.4138) = 2207
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f7' * 32}")

    html = response.get_data(as_text=True)
    assert 'style="width: 2207px; height: 48px;"' in html  # oscillogram wrap
    assert 'style="width: 2207px; height: 564px;"' in html  # spectrogram wrap


def test_recording_details_page_puts_oscillogram_above_spectrogram(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Matches the drawer panel's own convention (`_recording_panel.html`,
    CLAUDE.md's "Derived media rendering" section): the oscillogram is "a
    compact strip above" the main, taller spectrogram view, not below it.
    The two were the other way round here until 2026-09-02."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f6" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f6' * 32}")

    html = response.get_data(as_text=True)
    assert html.index('id="detail-oscillogram-wrap"') < html.index(
        'id="detail-spectrogram-wrap"',
    )


def test_recording_details_page_renders_one_img_per_tile(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f4" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=4.0,  # needs 3 tiles at DETAIL_MAX_TILE_WIDTH_PX=8000, DETAIL_PX_PER_MS~=4.4138
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f4' * 32}")

    html = response.get_data(as_text=True)
    assert html.count('class="detail-spectrogram-tile"') == 3
    assert html.count('class="detail-oscillogram-tile"') == 3
    assert "/detail-spectrogram/0.webp" in html
    assert "/detail-spectrogram/1.webp" in html
    assert "/detail-spectrogram/2.webp" in html
    assert "/detail-oscillogram/0.webp" in html
    assert "/detail-oscillogram/1.webp" in html
    assert "/detail-oscillogram/2.webp" in html


def test_recording_details_page_defaults_the_back_link_to_the_map(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f8" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f8' * 32}")

    html = response.get_data(as_text=True)
    assert '<a href="/">← Back to map</a>' in html


def test_recording_details_page_honours_a_return_to_query_param(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f9" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(
        f"/recordings/{'f9' * 32}?return_to=%2Fsessions%2F7",
    )

    html = response.get_data(as_text=True)
    assert '<a href="/sessions/7">← Back to sessions</a>' in html


def test_recording_details_page_explains_missing_metadata(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                # duration_s / samplerate_hz left unset
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f3' * 32}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "cannot render" in html.lower()
    assert "detail-spectrogram/0.webp" not in html


def test_recording_details_page_shows_a_placeholder_when_the_preview_is_not_yet_rendered(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Regression test: `params` (duration_s/samplerate_hz present) used to be
    the ONLY gate on the whole audio-controls block -- unlike the map
    drawer's `_recording_panel.html`, which separately checks
    `preview_path(...).exists()` (`preview_ready`, `web/views/map.py`). A
    recording can have its metadata (available immediately from the WAV
    header) well before its derived-media job actually renders
    `preview.opus` -- ingested-but-not-yet-processed is a normal, if narrow,
    timing window, and a PREVIEW_VERSION bump (2026-09-04, invalidating
    every existing preview at once for a re-render) makes that window wide
    and visible. Before this fix, the details page showed a fully working-
    looking TE/HET/play toolbar during that window that 404'd the instant
    you clicked play -- caught live, "Map page says audio preview is not
    processed yet - details page shows all the buttons. Inconsistent."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f7" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    # No preview.opus written under tmp_path / "media" -- not yet rendered.
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f7' * 32}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "audio preview not processed yet" in html.lower()
    assert 'class="audio-controls"' not in html
    # The spectrogram/oscillogram viewer doesn't depend on preview.opus at all
    # (it's rendered on demand from the WAV itself) -- must still show.
    assert f"/recordings/{'f7' * 32}/detail-spectrogram/0.webp" in html


def test_recording_details_page_renders_the_tool_toolbar(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="fa" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'fa' * 32}")

    html = response.get_data(as_text=True)
    assert (
        '<button type="button" class="tool-button" data-tool="default" '
        'aria-pressed="true">Default</button>' in html
    )
    assert (
        '<button type="button" class="tool-button" data-tool="ruler" '
        'aria-pressed="false">Ruler</button>' in html
    )


def test_recording_details_page_shows_a_link_to_the_map_centered_on_this_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="fc" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'fc' * 32}")

    html = response.get_data(as_text=True)
    assert f'href="/?recording={"fc" * 32}"' in html


def test_recording_details_page_shows_a_link_to_its_session_when_it_has_one(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
        )
        session.add(annotation_session)
        session.flush()
        session.add(
            Recording(
                audio_hash="fd" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
                session_id=annotation_session.id,
            ),
        )
        session.commit()
        session_id = annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'fd' * 32}")

    html = response.get_data(as_text=True)
    assert f'href="/sessions/{session_id}"' in html


def test_recording_details_page_omits_the_session_link_when_it_has_no_session(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="fe" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'fe' * 32}")

    html = response.get_data(as_text=True)
    assert "/sessions/" not in html


def test_recording_details_page_shows_the_favourite_toggle_unstarred_by_default(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="ff" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'ff' * 32}")

    html = response.get_data(as_text=True)
    assert 'id="detail-favourite"' in html
    assert 'aria-pressed="false"' in html
    assert "☆" in html


def test_recording_details_page_shows_the_favourite_toggle_starred(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
                favourite=True,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'a1' * 32}")

    html = response.get_data(as_text=True)
    assert 'aria-pressed="true"' in html
    assert "★" in html


def test_recording_details_page_loads_htmx(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Regression test: the page's favourite button carries `hx-post`/`hx-target`/
    `hx-swap` attributes but the page never included htmx.min.js -- only map.html did --
    so those attributes were inert and clicking the star did nothing. Caught live: "Fav
    button seems broken on the details page.\""""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'a3' * 32}")

    html = response.get_data(as_text=True)
    assert "vendor/htmx.min.js" in html


def test_toggle_favourite_with_panel_detail_returns_just_the_button_fragment(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().post(
        f"/recordings/{'a2' * 32}/favourite?panel=detail",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="detail-favourite"' in html
    assert 'aria-pressed="true"' in html
    assert "★" in html
    # Only the button, not the rest of the drawer panel fragment.
    assert "panel-header" not in html
    assert "waveform-grid" not in html

    with OrmSession(engine) as session:
        recording = session.get(Recording, 1)
        assert recording is not None
        assert recording.favourite is True


def test_manual_classification_route_sets_a_species_claim(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="j" * 64,
            path="j.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().post(
        f"/recordings/{'j' * 64}/manual-classification",
        data={"verdict": "species", "taxon_ids": [str(taxon_id)]},
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # `taxon.scientific_name` alone is not a real assertion -- it also
    # appears in every response's inlined taxon_search_index regardless of
    # what's actually classified. Assert on the chip itself, which only
    # exists in the #classifier-tags block when the taxon is selected.
    assert f'data-taxon-id="{taxon_id}"' in html
    assert "classifier-tags" in html


def test_manual_classification_route_sets_no_id_as_a_sentinel_chip(
    engine: Engine,
    tmp_path: Path,
) -> None:
    # No ID/Noise are chips, not toggle buttons (docs/style-guide.md's
    # explicit-Save rule -- see _classifier_box.html/classifier_box.js) --
    # a standing NO_ID verdict renders as a data-sentinel-verdict chip.
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="l" * 64,
            path="l.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().post(
        f"/recordings/{'l' * 64}/manual-classification",
        data={"verdict": "no_id"},
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    tags_block = html.split('id="classifier-tags"')[1].split("</div>")[0]
    assert 'data-sentinel-verdict="no_id"' in tags_block
    assert "data-taxon-id" not in tags_block


def test_manual_classification_route_reflects_only_manual_state_not_the_automatic_winner(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Task 5 review finding: the classifier box must edit ONLY the
    recording's own MANUAL claims, never an automatic classifier's winning
    result -- an automatic SPECIES claim must not render as an editable
    manual chip, and an automatic NOISE claim must not disable the search
    input (there would be no way to enter a correction)."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="m" * 64,
            path="m.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
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

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'m' * 64}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The automatic claim must not show up as a manual chip, and the search
    # input must not be disabled (no manual NO_ID/NOISE is standing).
    tags_block = html.split('id="classifier-tags"')[1].split("</div>")[0]
    assert "Eptesicus serotinus" not in tags_block
    search_tag = html.split('id="classifier-search"')[1].split(">")[0]
    assert "disabled" not in search_tag


def test_taxon_search_index_attribute_is_valid_json(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Regression for a real bug: Jinja's `tojson` filter escapes `<`, `>`,
    `&`, `'` for safe HTML embedding but NOT `"` -- inlining it into a
    double-quoted HTML attribute lets the JSON's own `"` characters
    terminate the attribute early, corrupting it. The attribute must be
    single-quoted so `tojson`'s own `'` escaping keeps it intact; this test
    parses the attribute back out of a real rendered page and confirms it
    round-trips through `json.loads` with the taxon this test seeded."""
    with OrmSession(engine) as session:
        taxon = Taxon(
            rank="species",
            scientific_name="Nyctalus noctula",
            common_name_en="Common Noctule",
        )
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="n" * 64,
            path="n.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'n' * 64}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    match = re.search(r"data-taxon-search-index='([^']*)'", html)
    assert match is not None, (
        "expected a single-quoted data-taxon-search-index attribute"
    )

    index = json.loads(match.group(1))
    assert any(entry["scientific_name"] == "Nyctalus noctula" for entry in index)


def test_manual_classification_route_rejects_inconsistent_input(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="k" * 64,
            path="k.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().post(
        f"/recordings/{'k' * 64}/manual-classification",
        data={"verdict": "no_id", "taxon_ids": ["1"]},
    )

    assert response.status_code == 400


def test_manual_classification_route_404s_for_unknown_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().post(
        f"/recordings/{'z' * 64}/manual-classification",
        data={"verdict": "no_id"},
    )

    assert response.status_code == 404


def test_identifications_box_renders_on_the_details_page(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="h" * 64,
            path="h.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        recording.identifications = [
            Identification(
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NO_ID,
            ),
            Identification(
                source=IdSource.EMT_WAMD,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
        ]
        session.add(recording)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'h' * 64}")
    html = response.get_data(as_text=True)

    assert "Identifications" in html
    assert "identification-passive" in html
    assert "identification-current" in html
    assert "passive, ignored" in html


def test_identifications_box_shows_the_taxon_name_for_a_manual_claim(
    engine: Engine,
    tmp_path: Path,
) -> None:
    # A MANUAL claim has no raw_label of its own (it's a structured taxon
    # pick, not a detector's raw code string) -- before identification_label
    # existed, the row fell back to the bare word "species" with no
    # indication of which one was actually chosen.
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="m" * 64,
            path="m.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        recording.identifications = [
            Identification(
                source=IdSource.MANUAL,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
        ]
        session.add(recording)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'m' * 64}")
    html = response.get_data(as_text=True)

    assert "Pipistrellus pipistrellus" in html
    assert "manual: species" not in html


def test_recording_details_page_tiles_are_not_natively_draggable(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A plain <img> is draggable by default -- clicking and dragging it triggers the
    browser's native image drag-and-drop instead of the page's own pan-by-drag, which is
    exactly the reported bug ("shows a drag cursor but does not drag")."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="fb" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'fb' * 32}")

    html = response.get_data(as_text=True)
    tile_tags = [
        tag
        for tag in re.findall(r"<img[^>]*>", html)
        if "detail-spectrogram-tile" in tag or "detail-oscillogram-tile" in tag
    ]
    assert tile_tags, "expected at least one tile <img>"
    for tag in tile_tags:
        assert 'draggable="false"' in tag
