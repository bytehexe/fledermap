from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import flask
import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.app import create_app
from fledermap.web.timefmt import local_datetime_seconds

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]

# Vendor icon SVGs (needed by any template calling `icon()`) are written by the centralized
# `_vendor_icons` autouse fixture in `tests/conftest.py` -- see its docstring.


def _create_app_with_one_recording(
    engine: Engine, tmp_path: Path, audio_hash: str
) -> tuple[flask.Flask, str]:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash=audio_hash,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    return app, audio_hash


def test_select_tool_button_is_present(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "se" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    assert 'data-tool="select"' in html


def test_view_lock_toggle_is_not_disabled_on_load(
    engine: Engine, tmp_path: Path
) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "vl" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    lock_button_start = html.index('id="view-lock-toggle"')
    lock_button_tag_end = html.index(">", lock_button_start)
    assert "disabled" not in html[lock_button_start:lock_button_tag_end]


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
    recorded_at = datetime(2026, 8, 25, 21, 0, 0, tzinfo=UTC)
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f2" * 32,
                path="a.wav",
                recorded_at=recorded_at,
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
    display_tz = app.config["DISPLAY_TIMEZONE"]
    assert local_datetime_seconds(recorded_at, display_tz) in html


def test_recording_details_page_shows_snr_when_computed(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, 0, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
                snr_db=12.34,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/recordings/{'f3' * 32}").get_data(as_text=True)

    assert "SNR: 12.3 dB" in html


def test_recording_details_page_shows_snr_not_yet_computed(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f4" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, 0, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/recordings/{'f4' * 32}").get_data(as_text=True)

    assert "SNR: not yet computed" in html


def test_recording_details_page_explains_a_missing_site_as_an_outlier(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A recording with a real position but no `site_id` (outside every
    derived site's radius) must say so explicitly rather than just omitting
    the "Site: ..." line -- otherwise a reader can't tell "outlier" apart
    from "the site info silently failed to load". Distinct from a recording
    with no position at all -- see the "no location data" test below --
    since only this one can actually be found by panning/zooming the map."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                geom=WKTElement("POINT(10 50)", srid=4326),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f3' * 32}")

    html = response.get_data(as_text=True)
    assert "No site (outlier)" in html


def test_recording_details_page_explains_a_missing_geom_as_no_location_data(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A recording with no `geom` at all (never had GPS/position metadata)
    must be told apart from a genuine geo-outlier -- "No site (outlier)"
    implies a real, findable position that just isn't clustered into a
    site, which is misleading here: the map has nothing to pan or zoom to
    for this recording at all (Obsidian backlog, 2026-09-09)."""
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
    assert "No location data" in html
    assert "No site (outlier)" not in html


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
    # zoom comes from DETAIL_COMFORT_MAX_HEIGHT_PX (300) / the spectrogram's
    # own 564px height -- both wraps share the same initial_zoom (Janna,
    # 2026-09-13: server and client must read one shared constant so their
    # sizes can never disagree).
    zoom = 300 / 564
    assert (
        f'style="width: 2207px; height: 58px; zoom: {zoom};"' in html
    )  # oscillogram wrap
    assert (
        f'style="width: 2207px; height: 564px; zoom: {zoom};"' in html
    )  # spectrogram wrap


@pytest.mark.parametrize("samplerate_hz", [256_000, 44_100, 192_000])
def test_recording_details_page_exposes_the_rasters_native_size_for_the_axis_math(
    engine: Engine,
    tmp_path: Path,
    samplerate_hz: int,
) -> None:
    """recording_detail.js derives every px <-> time/kHz conversion (axis ticks, Ruler,
    crosshair) from the spectrogram wrap's `data-native-*-px` and the tiles' real
    duration/max-frequency -- so those must equal the size the wrap is actually reserved at.
    44.1 kHz and 192 kHz matter: their `max_freq * 4.7` is not a whole number of pixels, so a
    scale taken from the nominal 4.7 px/kHz constant instead of the rounded raster height puts
    the bottom edge off 0 kHz."""
    audio_hash = f"{samplerate_hz:064x}"
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash=audio_hash,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=samplerate_hz,
            ),
        )
        session.commit()
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    html = app.test_client().get(f"/recordings/{audio_hash}").get_data(as_text=True)

    wrap = re.search(
        r'<div\s+class="detail-spectrogram-wrap".*?>', html, flags=re.DOTALL
    )
    assert wrap is not None
    wrap_tag = wrap.group(0)
    inline = re.search(r"width: (\d+)px; height: (\d+)px", wrap_tag)
    native_w = re.search(r'data-native-width-px="(\d+)"', wrap_tag)
    native_h = re.search(r'data-native-height-px="(\d+)"', wrap_tag)
    assert inline and native_w and native_h
    assert (native_w.group(1), native_h.group(1)) == inline.groups()
    tile = re.search(r"<img\s+class=\"detail-spectrogram-tile\".*?>", html, re.DOTALL)
    assert tile is not None
    max_freq = re.search(r'data-max-freq-khz="([\d.]+)"', tile.group(0))
    assert max_freq is not None
    assert float(max_freq.group(1)) == min(120.0, samplerate_hz / 2000)


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
    assert '<a href="/">' in html
    assert "icons-tabler-outline icon-tabler-chevron-left" in html
    assert "Back to map</a>" in html


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
    assert '<a href="/sessions/7">' in html
    assert "icons-tabler-outline icon-tabler-chevron-left" in html
    assert "Back to sessions</a>" in html


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
        '<button type="button" class="tool-button playback-only" data-tool="default" '
        'aria-pressed="true">Default</button>' in html
    )
    assert (
        '<button type="button" class="tool-button playback-only" data-tool="ruler" '
        'aria-pressed="false">Ruler</button>' in html
    )


def test_toolbar_has_three_zones(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="zn" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'zn' * 32}")
    html = response.get_data(as_text=True)

    assert 'class="toolbar-zone toolbar-zone-tools"' in html
    assert 'class="toolbar-zone toolbar-zone-shared"' in html
    assert 'class="toolbar-zone toolbar-zone-view"' in html
    # Default/Ruler/View Lock stay in the tools zone; Denoise moves to the shared zone.
    tools_zone_start = html.index('class="toolbar-zone toolbar-zone-tools"')
    shared_zone_start = html.index('class="toolbar-zone toolbar-zone-shared"')
    assert tools_zone_start < html.index('data-tool="default"') < shared_zone_start
    assert tools_zone_start < html.index('data-tool="ruler"') < shared_zone_start
    assert tools_zone_start < html.index('id="view-lock-toggle"') < shared_zone_start
    assert shared_zone_start < html.index('id="denoise-toggle"')


def test_view_toggle_button_is_present_and_targets_analysis(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="vt" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'vt' * 32}")
    html = response.get_data(as_text=True)

    assert 'id="view-toggle-button"' in html
    assert 'data-target-view="analysis"' in html
    assert 'class="analysis-panel"' in html


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
    assert f'href="/?recording={"fc" * 32}&amp;verdict=all"' in html


def test_recording_details_page_map_link_shows_a_recording_the_default_verdict_filter_hides(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The default map view only shows SPECIES-verdict recordings
    (`_passes_verdict_filter`'s permissive default is narrower than "no filter").
    A recording with no identification at all would 404 as "not found" on
    a fresh `/?recording=<hash>` navigation without the `verdict=all` override
    baked into the "Show on map" link."""
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
    client = app.test_client()

    href = f"/?recording={'fe' * 32}&amp;verdict=all"
    assert href in client.get(f"/recordings/{'fe' * 32}").get_data(as_text=True)

    panel = client.get(f"/recordings/{'fe' * 32}/panel?verdict=all").get_data(
        as_text=True
    )
    assert "Recording not found" not in panel


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
    assert "icons-tabler-outline icon-tabler-star" in html


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
    assert "icons-tabler-filled icon-tabler-star" in html


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
    assert "icons-tabler-filled icon-tabler-star" in html
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
        taxon_id = taxon.id
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
                taxon_id=taxon_id,
            ),
        ]
        session.add(recording)
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        combine_emt_display=False,
    )
    response = app.test_client().get(f"/recordings/{'h' * 64}")
    html = response.get_data(as_text=True)

    assert "Identifications" in html
    assert "identification-passive" in html
    assert "identification-current" in html
    assert "shadowed by" not in html  # dropped in favor of styling + precedence order
    # Style-guide audit (2026-09-09): a mapped taxon's name in the Identifications
    # breakdown must be a link (docs/style-guide.md's "wherever an entity's name is
    # written, it should be a link" rule) -- it was plain text before.
    assert f'<a href="/species/{taxon_id}">' in html


def test_identifications_box_orders_rows_by_source_precedence(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """emt.guano outranks emt.wamd in `current_best._PRECEDENCE`, so it must
    render first even though it's the passive (NO_ID, skipped) row here and
    emt.wamd is the one actually winning -- position reflects source
    precedence, not which row happens to be "current"."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="j" * 64,
            path="j.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        recording.identifications = [
            Identification(
                source=IdSource.EMT_WAMD,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
            Identification(
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NO_ID,
            ),
        ]
        session.add(recording)
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        combine_emt_display=False,
    )
    response = app.test_client().get(f"/recordings/{'j' * 64}")
    html = response.get_data(as_text=True)

    assert html.index("emt.guano") < html.index("emt.wamd")


def test_identifications_box_combines_emt_sources_into_one_row_by_default(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="q" * 64,
            path="q.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        recording.identifications = [
            Identification(
                source=IdSource.EMT_WAMD,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
            Identification(
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
        ]
        session.add(recording)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'q' * 64}")
    html = response.get_data(as_text=True)

    assert 'title="emt"' in html
    assert 'title="emt.guano"' not in html
    assert 'title="emt.wamd"' not in html


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


def test_details_page_has_no_prevnext_with_no_review_param(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'b1' * 32}")

    html = response.get_data(as_text=True)
    assert "Previous" not in html
    assert "Next" not in html
    assert "Reviewing flagged recordings" not in html


def test_details_page_shows_review_snapshot_prevnext_and_banner(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        first = Recording(
            audio_hash="b2" * 32,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )
        second = Recording(
            audio_hash="b3" * 32,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        session.add_all([first, second])
        session.commit()
        first_id, second_id = first.id, second.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(
        f"/recordings/{'b2' * 32}?review={first_id},{second_id}",
    )

    html = response.get_data(as_text=True)
    assert "Reviewing flagged recordings" in html
    assert "1 of 2" in html
    assert f"/recordings/{'b3' * 32}?review={first_id},{second_id}" in html
    # "Previous" still renders at the start of the snapshot -- disabled, not
    # absent, so "Next" never jumps position depending on whether a
    # "Previous" link exists next to it (style guide's disable-don't-hide
    # rule).
    assert "icons-tabler-outline icon-tabler-chevron-left" in html
    assert "Previous" in html
    assert html.count('class="is-disabled"') == 1
    assert 'href="#"' in html


def test_details_page_review_snapshot_ignores_the_current_review_status(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The snapshot is fixed at session start -- landing on the SECOND item
    after the first was just reclassified (dropped out of needs_review)
    must not affect this page's own prev/next, since it never re-queries
    needs_review at all."""
    with OrmSession(engine) as session:
        first = Recording(
            audio_hash="b4" * 32,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )
        second = Recording(
            audio_hash="b5" * 32,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            flagged_for_review=False,  # already "resolved" -- no longer needs review
        )
        session.add_all([first, second])
        session.commit()
        first_id, second_id = first.id, second.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(
        f"/recordings/{'b5' * 32}?review={first_id},{second_id}",
    )

    html = response.get_data(as_text=True)
    assert "2 of 2" in html
    assert f"/recordings/{'b4' * 32}?review={first_id},{second_id}" in html
    assert "icons-tabler-outline icon-tabler-chevron-left" in html
    assert "Previous" in html  # a real prior item still exists
    # "Next" still renders at the end -- disabled, not absent (see the
    # "1 of 2" test above for why).
    assert "icons-tabler-outline icon-tabler-chevron-right" in html
    assert "Next" in html
    assert html.count('class="is-disabled"') == 1
    assert 'href="#"' in html
    # Landing on the last item of a real multi-item snapshot must still show
    # the "no more flagged recordings" note -- previously this only rendered
    # when BOTH previous and next were absent (i.e. only for a one-item
    # snapshot), so a working "Previous" link and the note never appeared
    # together even though the queue genuinely ended here.
    assert "No more flagged recordings" in html


def test_details_page_no_more_flagged_recordings_note_at_the_end(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b6" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
        recording_id = session.scalars(select(Recording)).one().id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'b6' * 32}?review={recording_id}")

    html = response.get_data(as_text=True)
    assert "No more flagged recordings" in html
    assert 'href="/reviews"' in html


def test_details_page_hides_back_to_map_during_a_review_session(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """ "Exit review" (in the review banner) is the correct way out of a
    review session -- a second, differently-worded "Back to map" link right
    above it is confusing, not merely redundant (Janna, 2026-09-11)."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b8" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
        recording_id = session.scalars(select(Recording)).one().id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    outside_review = app.test_client().get(f"/recordings/{'b8' * 32}")
    assert "Back to map" in outside_review.get_data(as_text=True)

    during_review = app.test_client().get(
        f"/recordings/{'b8' * 32}?review={recording_id}",
    )
    html = during_review.get_data(as_text=True)
    assert "Back to map" not in html
    assert "Exit review" in html


def test_details_page_header_actions_order_is_flag_then_favourite_rightmost(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Project-wide placement decided in docs/style-guide.md's
    ".entity-header"/".panel-header" section (Janna, 2026-09-11): both
    toggles sit to the right of the page's own action links (Show on map,
    Session), flag before favourite, favourite always right-most."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b9" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/recordings/{'b9' * 32}").get_data(as_text=True)

    show_on_map = html.index("Show on map")
    flag_button = html.index('class="flag-toggle"')
    favourite_button = html.index('class="favourite-toggle"')
    assert show_on_map < flag_button < favourite_button


def test_details_page_review_param_not_containing_current_recording_is_ignored(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b7" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'b7' * 32}?review=999999")

    html = response.get_data(as_text=True)
    assert "Reviewing flagged recordings" not in html
    assert "Previous" not in html


def test_details_page_still_shows_the_flagged_for_review_section(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Unlike the drawer panel (see test_map_view.py's
    test_recording_panel_never_shows_the_flagged_for_review_section), the
    full-page details view keeps the complete "Flagged for review" section
    with its reasons list -- Obsidian backlog: removed from the overview
    for being "too much there", explicitly kept here."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Rare species")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="d1" * 32,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=recording.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/recordings/{'d1' * 32}").get_data(as_text=True)

    assert "Flagged for review" in html
    assert "Rare species" in html or "review-reasons" in html


def test_details_page_flag_button_states(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Same three-state icon logic as the drawer panel's flag button
    (docs/style-guide.md's drawer/detail parity)."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Rare species")
        session.add(taxon)
        session.flush()
        plain = Recording(
            audio_hash="d2" * 32,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        auto_flagged = Recording(
            audio_hash="d3" * 32,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        manually_flagged = Recording(
            audio_hash="d4" * 32,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            flagged_for_review=True,
        )
        session.add_all([plain, auto_flagged, manually_flagged])
        session.flush()
        for r in (auto_flagged, manually_flagged):
            session.add(
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon.id,
                    first_seen_at=r.recorded_at,
                ),
            )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    plain_html = client.get(f"/recordings/{'d2' * 32}").get_data(as_text=True)
    assert "icons-tabler-outline icon-tabler-flag" in plain_html
    assert "icon-tabler-flag-cog" not in plain_html

    auto_html = client.get(f"/recordings/{'d3' * 32}").get_data(as_text=True)
    assert "icons-tabler-outline icon-tabler-flag-cog" in auto_html

    manual_html = client.get(f"/recordings/{'d4' * 32}").get_data(as_text=True)
    assert "icons-tabler-filled icon-tabler-flag" in manual_html
    assert "icon-tabler-flag-cog" not in manual_html


def test_toggle_flag_for_review_detail_panel_shows_flag_cog_after_unflagging_while_still_auto_flagged(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The `panel=detail` POST branch (web/views/map.py's
    toggle_flag_for_review) must compute `reasons` itself -- it renders only
    `_detail_flag_button.html`, not the whole page, so nothing else would
    supply it."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Rare species")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="d5" * 32,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            flagged_for_review=True,
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=recording.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().post(
        f"/recordings/{'d5' * 32}/flag-for-review?panel=detail",
    )

    html = response.get_data(as_text=True)
    assert "icons-tabler-outline icon-tabler-flag-cog" in html


def test_analysis_panel_has_whole_file_and_selection_controls(
    engine: Engine, tmp_path: Path
) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "ap" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    assert 'data-span="whole-file"' in html
    assert 'data-span="selection"' in html
    assert 'id="sound-level-spectrum-chart"' in html
    assert "Call-distance graph" in html


def test_sound_level_canvas_sits_alone_in_a_dedicated_chart_wrapper(
    engine: Engine, tmp_path: Path
) -> None:
    """Chart.js needs the canvas in its own container whose size does not come from the canvas
    (app.css gives `.analysis-chart-wrap` a fixed aspect ratio) -- otherwise the chart resizes
    down with its panel but never grows back. The actual resizing needs a real browser; this pins
    the markup the CSS depends on."""
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "cw" * 32)
    html = app.test_client().get(f"/recordings/{audio_hash}").get_data(as_text=True)

    canvas_at = html.index('id="sound-level-spectrum-chart"')
    wrap_at = html.rindex('<div class="analysis-chart-wrap">', 0, canvas_at)
    assert (
        "</div>" not in html[wrap_at:canvas_at]
    )  # the canvas is the wrapper's direct child
    wrap_end = html.index("</div>", canvas_at)
    assert (
        "<"
        not in html[
            html.index("</canvas>", canvas_at) + len("</canvas>") : wrap_end
        ].strip()
    )


def test_span_buttons_live_in_the_toolbar_tools_zone_as_analysis_only(
    engine: Engine, tmp_path: Path
) -> None:
    """Whole file / Selection are a view-level control (they will drive both Analysis graphs),
    so they sit in the toolbar's view-specific left zone, shown only in Analysis view via the
    `analysis-only` hook -- not inside one graph's own panel. The runtime show/hide needs a DOM;
    this checks the markup placement and hook."""
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "sz" * 32)
    html = app.test_client().get(f"/recordings/{audio_hash}").get_data(as_text=True)

    zone_start = html.index("toolbar-zone-tools")
    zone_end = html.index("toolbar-zone-shared")
    for needle in ('data-span="whole-file"', 'data-span="selection"'):
        position = html.index(needle)
        assert zone_start < position < zone_end, needle
        tag_start = html.rindex("<button", 0, position)
        assert "analysis-only" in html[tag_start:position], needle

    assert "analysis-graph-controls" not in html


def test_selection_span_button_is_disabled_on_load(
    engine: Engine, tmp_path: Path
) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "sd" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    selection_button_start = html.index('data-span="selection"')
    button_tag_start = html.rindex("<button", 0, selection_button_start)
    button_tag_end = html.index(">", selection_button_start)
    assert "disabled" in html[button_tag_start:button_tag_end]


def test_playback_only_tools_are_marked_for_hiding_in_analysis_view(
    engine: Engine, tmp_path: Path
) -> None:
    """`.view-analysis .toolbar-zone-tools .playback-only { display: none; }` (app.css) is the
    CSS hook that hides Default/Ruler/Select/View Lock when the Analysis view is active (design
    spec section 3) -- this only checks the markup carries the hook; the runtime hide/show
    behavior itself needs a DOM, which this project's Python test suite doesn't have."""
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "po" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)

    for needle in ('data-tool="default"', 'data-tool="ruler"', 'data-tool="select"'):
        tag_start = html.rindex("<button", 0, html.index(needle))
        tag_end = html.index(">", tag_start)
        assert "playback-only" in html[tag_start:tag_end], needle

    lock_tag_start = html.rindex("<button", 0, html.index('id="view-lock-toggle"'))
    lock_tag_end = html.index(">", lock_tag_start)
    assert "playback-only" in html[lock_tag_start:lock_tag_end]
