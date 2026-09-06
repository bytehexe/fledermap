"""Guards against the class of gap a `Config.from_env` setting can fall into:
added without a corresponding row in
`docs/reference/configuration.md`'s settings table (CLAUDE.md names that file
as the authority for "every `FLEDERMAP_*` setting"). Deliberately simple -- a
substring check against the raw file text, not a markdown-table parse -- so
it stays cheap to keep passing as new settings are added."""

from __future__ import annotations

from pathlib import Path

from fledermap.config import _KNOWN_FILE_KEYS

CONFIGURATION_DOC = (
    Path(__file__).parent.parent / "docs" / "reference" / "configuration.md"
)


def test_every_known_config_file_key_is_documented_in_configuration_md() -> None:
    text = CONFIGURATION_DOC.read_text()
    missing = [key for key in _KNOWN_FILE_KEYS if key not in text]
    assert not missing, (
        f"docs/reference/configuration.md is missing config key(s): {missing}"
    )
