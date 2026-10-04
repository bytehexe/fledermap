from __future__ import annotations

import re
from pathlib import Path

APP_CSS = (
    Path(__file__).parent.parent / "src" / "fledermap" / "web" / "static" / "app.css"
)


def test_color_danger_is_defined_in_light_and_both_dark_blocks() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    assert len(re.findall(r"--color-danger:\s*#[0-9a-fA-F]{6};", css)) == 3
