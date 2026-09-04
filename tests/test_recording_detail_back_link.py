from __future__ import annotations

from fledermap.web.views.recording_detail import _resolve_back_link


def test_resolve_back_link_defaults_to_the_map_when_return_to_is_missing() -> None:
    assert _resolve_back_link(None) == ("Back to map", "/")


def test_resolve_back_link_labels_the_map_and_preserves_its_filters() -> None:
    assert _resolve_back_link("/?site=3&species=PIPPIP") == (
        "Back to map",
        "/?site=3&species=PIPPIP",
    )


def test_resolve_back_link_labels_a_session_page() -> None:
    assert _resolve_back_link("/sessions/7") == ("Back to sessions", "/sessions/7")


def test_resolve_back_link_falls_back_to_a_generic_label_for_an_unrecognised_path() -> (
    None
):
    assert _resolve_back_link("/somewhere-else") == ("Back", "/somewhere-else")


def test_resolve_back_link_rejects_a_protocol_relative_url() -> None:
    """`//evil.example` is not a same-origin relative path -- the browser
    would treat it as `https://evil.example`. Must fall back to the default
    rather than sending the user off-site."""
    assert _resolve_back_link("//evil.example") == ("Back to map", "/")


def test_resolve_back_link_rejects_a_value_that_is_not_a_relative_path() -> None:
    assert _resolve_back_link("https://evil.example") == ("Back to map", "/")


def test_resolve_back_link_rejects_a_backslash_disguised_protocol_relative_url() -> (
    None
):
    """Browsers normalize a leading `\\` to `/` while parsing a URL for
    http(s) (WHATWG URL spec), so `/\\evil.example` is parsed identically to
    `//evil.example` -- a naive `startswith("//")` check alone misses this
    bypass."""
    assert _resolve_back_link("/\\evil.example") == ("Back to map", "/")


def test_resolve_back_link_rejects_a_tab_disguised_protocol_relative_url() -> None:
    """Browsers strip ASCII tab/CR/LF from a URL before parsing it, so a
    query value with a raw tab between the slashes (delivered as `%09`,
    already decoded by the time the route sees it) still becomes
    `//evil.example` once the browser parses the resulting href."""
    assert _resolve_back_link("/\t/evil.example") == ("Back to map", "/")
