from __future__ import annotations

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.search import ParsedQuery, parse_search_query


def test_empty_query_has_no_terms_and_no_warnings() -> None:
    parsed = parse_search_query("")
    assert parsed == ParsedQuery(
        free_text_terms=[],
        species_value=None,
        annotation_source=None,
        site_name=None,
        verdict=None,
        is_favourite=False,
        is_flagged=False,
        is_labeled=False,
        tags=[],
        warnings=[],
    )


def test_plain_words_become_free_text_terms() -> None:
    parsed = parse_search_query("pipistrellus buzz")
    assert parsed.free_text_terms == ["pipistrellus", "buzz"]
    assert parsed.warnings == []


def test_quoted_phrase_becomes_one_free_text_term() -> None:
    parsed = parse_search_query('"Kleiner Wald" foo')
    assert parsed.free_text_terms == ["Kleiner Wald", "foo"]


def test_species_field_unquoted() -> None:
    parsed = parse_search_query("species:PIPPIP")
    assert parsed.species_value == "PIPPIP"
    assert parsed.free_text_terms == []


def test_species_field_quoted() -> None:
    parsed = parse_search_query('species:"Pipistrellus pipistrellus"')
    assert parsed.species_value == "Pipistrellus pipistrellus"


def test_annotation_field_bd2() -> None:
    parsed = parse_search_query("annotation:bd2")
    assert parsed.annotation_source == IdSource.BATDETECT2
    assert parsed.warnings == []


def test_annotation_field_emt_sentinel() -> None:
    parsed = parse_search_query("annotation:emt")
    assert parsed.annotation_source == "emt"


def test_annotation_field_unknown_value_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("annotation:foo")
    assert parsed.annotation_source is None
    assert parsed.free_text_terms == ["annotation:foo"]
    assert parsed.warnings == [
        "Unknown annotation source 'foo' — searched as text instead."
    ]


def test_site_field_quoted() -> None:
    parsed = parse_search_query('site:"Kleiner Wald"')
    assert parsed.site_name == "Kleiner Wald"


def test_site_field_with_curly_quotes_parses_the_same_as_ascii_quotes() -> None:
    """iOS/macOS smart-punctuation and German „…" typing produce curly quotes instead of ASCII
    `"` -- `site:` has no closed vocabulary to diverge against, so without normalization this
    would silently mis-tokenize with no warning at all."""
    parsed = parse_search_query("site:„Kleiner Wald“")
    assert parsed == parse_search_query('site:"Kleiner Wald"')
    assert parsed.site_name == "Kleiner Wald"


def test_verdict_field_valid() -> None:
    parsed = parse_search_query("verdict:species")
    assert parsed.verdict == Verdict.SPECIES


def test_verdict_field_accepts_unidentified_and_all() -> None:
    assert parse_search_query("verdict:unidentified").verdict == "unidentified"
    assert parse_search_query("verdict:all").verdict == "all"


def test_verdict_field_invalid_value_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("verdict:foo")
    assert parsed.verdict is None
    assert parsed.free_text_terms == ["verdict:foo"]
    assert parsed.warnings == ["Unknown verdict 'foo' — searched as text instead."]


def test_is_favourite_flagged_labeled() -> None:
    parsed = parse_search_query("is:favourite is:flagged is:labeled")
    assert parsed.is_favourite is True
    assert parsed.is_flagged is True
    assert parsed.is_labeled is True
    assert parsed.warnings == []


def test_is_field_invalid_value_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("is:foo")
    assert parsed.is_favourite is False
    assert parsed.free_text_terms == ["is:foo"]
    assert parsed.warnings == ["Unknown is: value 'foo' — searched as text instead."]


def test_hash_token_is_a_tag_not_free_text() -> None:
    parsed = parse_search_query("#buzz")
    assert parsed.tags == ["buzz"]
    assert parsed.free_text_terms == []
    assert parsed.warnings == []


def test_hash_token_lowercases_the_tag() -> None:
    assert parse_search_query("#Buzz").tags == ["buzz"]


def test_hash_token_with_colon_value_is_a_single_tag() -> None:
    parsed = parse_search_query("#device:emt2")
    assert parsed.tags == ["device:emt2"]
    assert parsed.free_text_terms == []


def test_hash_token_is_never_treated_as_field_value_grammar() -> None:
    """`#a:b` must be one tag, never the field:value grammar (a field name can never start with
    `#`, but this pins the "# always means a tag search" rule down explicitly)."""
    parsed = parse_search_query("#a:b")
    assert parsed.tags == ["a:b"]
    assert parsed.site_name is None
    assert parsed.species_value is None


def test_multiple_hash_tokens_are_all_collected() -> None:
    parsed = parse_search_query("#buzz #social-call")
    assert parsed.tags == ["buzz", "social-call"]


def test_invalid_hash_token_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("#")
    assert parsed.tags == []
    assert parsed.free_text_terms == ["#"]
    assert parsed.warnings == ["Invalid tag '#' — searched as text instead."]


def test_unwhitelisted_field_name_demotes_to_free_text_with_warning() -> None:
    parsed = parse_search_query("spceies:PIPPIP")
    assert parsed.species_value is None
    assert parsed.free_text_terms == ["spceies:PIPPIP"]
    assert parsed.warnings == ["Unknown field 'spceies' — searched as text instead."]


def test_colon_shaped_free_text_still_just_a_word() -> None:
    """A token that looks field-shaped but whose prefix was never meant as a field (a timestamp)
    -- still gets the same harmless warning, but the search still runs as plain free text either
    way (see the spec's "heuristic nudge, not a hard error" note)."""
    parsed = parse_search_query("12:30")
    assert parsed.free_text_terms == ["12:30"]
    assert parsed.warnings == ["Unknown field '12' — searched as text instead."]


def test_mismatched_quote_falls_back_to_plain_free_text() -> None:
    parsed = parse_search_query('site:"Kleiner Wald bbb ccc')
    assert parsed.site_name is None
    assert parsed.free_text_terms == ['site:"Kleiner', "Wald", "bbb", "ccc"]
    assert parsed.warnings == ["Unmatched quote — showing plain-text results."]


def test_aaa_field_value_bbb_tokenizes_as_three_separate_tokens() -> None:
    """Regression test for the greedy single-regex-pass bug found during design: a field token in
    the middle of free text must not consume neighboring words."""
    parsed = parse_search_query("aaa species:PIPPIP bbb")
    assert parsed.free_text_terms == ["aaa", "bbb"]
    assert parsed.species_value == "PIPPIP"


def test_mixed_free_text_and_fields() -> None:
    parsed = parse_search_query("buzz species:PIPPIP is:favourite")
    assert parsed.free_text_terms == ["buzz"]
    assert parsed.species_value == "PIPPIP"
    assert parsed.is_favourite is True


def test_repeated_field_last_one_wins() -> None:
    """The grammar has no stated precedence for a field given twice -- last-one-wins is the
    chosen, deterministic behavior (Review Focus item in the plan)."""
    parsed = parse_search_query("verdict:species verdict:noise")
    assert parsed.verdict == Verdict.NOISE
