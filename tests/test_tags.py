from fledermap.domain.tags import extract_tags


def test_extracts_a_plain_tag() -> None:
    assert extract_tags("some text #buzz more text") == {"buzz"}


def test_extracts_a_tag_with_hyphen() -> None:
    assert extract_tags("#tag-with-minus") == {"tag-with-minus"}


def test_extracts_a_tag_with_colon_value() -> None:
    assert extract_tags("#device:emt2") == {"device:emt2"}


def test_colon_followed_by_space_is_not_part_of_the_tag() -> None:
    assert extract_tags("#TODO: check this") == {"todo"}


def test_colon_at_end_of_text_is_not_part_of_the_tag() -> None:
    assert extract_tags("#TODO:") == {"todo"}


def test_does_not_match_a_hash_embedded_mid_word() -> None:
    assert extract_tags("see issue#42 for details") == set()


def test_matches_a_hash_at_the_very_start_of_the_text() -> None:
    assert extract_tags("#buzz at the start") == {"buzz"}


def test_is_case_insensitive() -> None:
    assert extract_tags("#Buzz") == {"buzz"}


def test_multiple_tags() -> None:
    assert extract_tags("#buzz and #social-call here") == {"buzz", "social-call"}


def test_no_tags_returns_empty_set() -> None:
    assert extract_tags("just a plain note, nothing tagged") == set()


def test_bare_hash_with_nothing_after_is_not_a_tag() -> None:
    assert extract_tags("price is #") == set()
