from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.services.sun import MoonPhaseBucket
from fledermap.web.icons import (
    IconNotFoundError,
    make_icon_global,
    make_moon_phase_icon_global,
)


def test_icon_reads_the_outline_file_by_default(tmp_path: Path) -> None:
    (tmp_path / "icons" / "outline").mkdir(parents=True)
    (tmp_path / "icons" / "outline" / "star.svg").write_text("<svg>outline-star</svg>")

    icon = make_icon_global(tmp_path)

    assert str(icon("star")) == "<svg>outline-star</svg>"


def test_icon_reads_the_filled_file_when_requested(tmp_path: Path) -> None:
    (tmp_path / "icons" / "filled").mkdir(parents=True)
    (tmp_path / "icons" / "filled" / "star.svg").write_text("<svg>filled-star</svg>")

    icon = make_icon_global(tmp_path)

    assert str(icon("star", filled=True)) == "<svg>filled-star</svg>"


def test_icon_is_marked_safe_for_jinja_autoescape(tmp_path: Path) -> None:
    """The whole point of icon() is to inline raw SVG markup -- Jinja's autoescape would
    otherwise turn every `<` into `&lt;` and the icon would render as literal text instead of
    an image. markupsafe.Markup is what tells Jinja "this is already-safe HTML, don't escape
    it"."""
    from markupsafe import Markup

    (tmp_path / "icons" / "outline").mkdir(parents=True)
    (tmp_path / "icons" / "outline" / "star.svg").write_text("<svg>x</svg>")

    icon = make_icon_global(tmp_path)

    assert isinstance(icon("star"), Markup)


def test_icon_raises_loudly_when_the_file_is_missing(tmp_path: Path) -> None:
    """Fail loud, not silent -- same posture as this project's missing-ffmpeg/pg_dump checks
    elsewhere. A silently-empty icon would ship a visibly broken button with no error anywhere."""
    icon = make_icon_global(tmp_path)

    with pytest.raises(IconNotFoundError, match="nonexistent"):
        icon("nonexistent")


def test_moon_phase_icon_reads_the_file_for_the_given_bucket(tmp_path: Path) -> None:
    (tmp_path / "moon-phase-full.svg").write_text("<svg>full-moon</svg>")

    moon_phase_icon = make_moon_phase_icon_global(tmp_path)

    assert str(moon_phase_icon(MoonPhaseBucket.FULL)) == "<svg>full-moon</svg>"


def test_moon_phase_icon_is_marked_safe_for_jinja_autoescape(tmp_path: Path) -> None:
    from markupsafe import Markup

    (tmp_path / "moon-phase-new.svg").write_text("<svg>x</svg>")

    moon_phase_icon = make_moon_phase_icon_global(tmp_path)

    assert isinstance(moon_phase_icon(MoonPhaseBucket.NEW), Markup)


def test_moon_phase_icon_raises_loudly_when_the_file_is_missing(tmp_path: Path) -> None:
    moon_phase_icon = make_moon_phase_icon_global(tmp_path)

    with pytest.raises(IconNotFoundError, match="full"):
        moon_phase_icon(MoonPhaseBucket.FULL)
