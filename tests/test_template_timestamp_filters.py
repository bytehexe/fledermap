"""Every displayed timestamp must go through one of `timefmt.py`'s filters
(`local_datetime`/`local_date`/`local_datetime_seconds`), never a bare
`.strftime()`/`.isoformat()` call baked into a template -- see
docs/style-guide.md's "Timestamp display" section. Plain filesystem+string
check, no database needed."""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "fledermap" / "web" / "templates"
)


def test_no_template_calls_strftime_or_isoformat_directly() -> None:
    offenders = []
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        text = path.read_text()
        if ".strftime(" in text or ".isoformat()" in text:
            offenders.append(path.name)
    assert offenders == []
