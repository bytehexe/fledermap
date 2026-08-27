"""Guards against the class of gap `FLEDERMAP_TRANSECT_DISTANCE_M` fell into:
a `Config.from_env` setting added without a corresponding row in
`docs/setup.md`'s settings table (CLAUDE.md names that file as the authority
for "every `FLEDERMAP_*` setting"). Deliberately simple -- a substring check
against the raw file text, not a markdown-table parse -- so it stays cheap to
keep passing as new settings are added."""

from __future__ import annotations

from pathlib import Path

from fledermap.config import _KNOWN_FILE_KEYS

SETUP_DOC = Path(__file__).parent.parent / "docs" / "setup.md"


def test_every_known_config_file_key_is_documented_in_setup_md() -> None:
    text = SETUP_DOC.read_text()
    missing = [key for key in _KNOWN_FILE_KEYS if key not in text]
    assert not missing, f"docs/setup.md is missing config key(s): {missing}"
