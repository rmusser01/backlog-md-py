"""Tests for :mod:`backlog_py.search.simple`.

The scoring helpers here back task search (``core.repository``), document search
(``core.documents``) and decision search (``core.decisions``), which in turn back
the CLI, the MCP ``task_search`` tool, the TUI and the browser UI.  Two things are
pinned below:

* non-ASCII scripts (CJK, Cyrillic, Greek, Hebrew, accented Latin) must be
  searchable at all, and
* the existing ASCII scoring tiers/constants must not move, so downstream
  ordering stays byte-for-byte identical for ASCII queries.
"""

from __future__ import annotations

import pytest

from backlog_py.search.simple import contains_query, query_score, ranked_matches


# --------------------------------------------------------------------------- #
# ASCII regression guards: exact scores are pinned on purpose.
# --------------------------------------------------------------------------- #


def test_ascii_exact_match_scores_token_plus_whole_string_bonus():
    assert query_score("parse", "parse") == 3000


def test_ascii_prefix_match_scores_below_exact():
    assert query_score("parser", "parse") == 1599


def test_ascii_substring_match_scores_below_prefix():
    assert query_score("reparse", "parse") == 1498


def test_ascii_subsequence_match_scores_below_substring():
    assert query_score("parkside", "parse") == 647


def test_ascii_fuzzy_match_scores_below_subsequence():
    assert query_score("parsr", "parse") == 500


def test_ascii_tiers_stay_in_descending_order():
    query = "parse"
    tiers = [
        query_score("parse", query),
        query_score("parser", query),
        query_score("reparse", query),
        query_score("parkside", query),
        query_score("parsr", query),
    ]
    assert all(score is not None for score in tiers)
    assert tiers == sorted(tiers, reverse=True)
    assert len(set(tiers)) == len(tiers)


def test_ascii_word_boundary_bonus_beats_bare_substring():
    # " parse " inside " parse tree " outranks "parse" inside "reparser".
    assert query_score("parse tree", "parse") > query_score("reparser", "parse")


def test_ascii_non_match_returns_none():
    assert query_score("parse", "zzzz") is None


def test_multi_token_query_requires_every_token_to_match():
    assert query_score("parser preservation", "parser preservation") is not None
    assert query_score("parser preservation", "parser absentee") is None


def test_tokens_split_on_underscore_and_punctuation():
    assert query_score("foo_bar-baz", "bar") is not None
    assert query_score("foo_bar-baz", "baz") is not None


def test_contains_query_mirrors_query_score():
    assert contains_query("parser preservation", "parser") is True
    assert contains_query("parser preservation", "zzzz") is False


# --------------------------------------------------------------------------- #
# Empty / degenerate inputs (documented behaviour, preserved).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_empty_query_returns_zero(query):
    assert query_score("any text at all", query) == 0
    assert contains_query("any text at all", query) is True


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_text_returns_none(text):
    assert query_score(text, "parse") is None


def test_text_without_word_characters_returns_none():
    assert query_score("--- !!! ---", "parse") is None


def test_query_without_word_characters_returns_none():
    assert query_score("parser preservation", "!!!") is None


# --------------------------------------------------------------------------- #
# CJK (the headline bug): ideographic scripts have no spaces, so whole-run
# tokens mean prefix/substring matching carries all the weight.
# --------------------------------------------------------------------------- #


def test_cjk_prefix_query_matches_cjk_title():
    assert query_score("日本語のタスク", "日本") is not None


def test_cjk_interior_substring_query_matches_cjk_title():
    assert query_score("日本語のタスク", "タスク") is not None


def test_cjk_full_title_query_matches():
    assert query_score("日本語のタスク", "日本語のタスク") is not None


def test_cjk_scoring_tiers_are_ordered_exact_then_prefix_then_interior():
    text = "日本語のタスク"
    exact = query_score(text, text)
    prefix = query_score(text, "日本")
    interior = query_score(text, "タスク")
    assert exact is not None and prefix is not None and interior is not None
    assert exact > prefix > interior


def test_cjk_unrelated_query_does_not_match():
    assert query_score("日本語のタスク", "韓国") is None


def test_cjk_query_matches_when_embedded_next_to_ascii():
    assert query_score("task日本語parser", "日本語") is not None


def test_halfwidth_katakana_query_matches_fullwidth_text():
    assert query_score("キャンセル済み", "ｷｬﾝｾﾙ") is not None
    assert query_score("ｷｬﾝｾﾙ済み", "キャンセル") is not None


def test_hangul_prefix_query_matches():
    assert query_score("한국어 텍스트", "한국") is not None


# --------------------------------------------------------------------------- #
# Cyrillic / Greek / Hebrew.
# --------------------------------------------------------------------------- #


def test_cyrillic_query_matches_cyrillic_title():
    assert query_score("Задача один", "Задача") is not None


def test_cyrillic_query_is_case_insensitive():
    assert query_score("Задача один", "задача") == query_score("задача один", "ЗАДАЧА")


def test_cyrillic_prefix_query_matches():
    assert query_score("Задача один", "Зада") is not None


def test_cyrillic_unrelated_query_does_not_match():
    assert query_score("Задача один", "документ") is None


def test_greek_query_matches_ignoring_accents():
    assert query_score("Ελληνικά κείμενο", "ελληνικα") is not None
    assert query_score("Ελληνικα κείμενο", "Ελληνικά") is not None


def test_hebrew_query_matches_hebrew_title():
    assert query_score("משימה חדשה", "משימה") is not None


# --------------------------------------------------------------------------- #
# Accented Latin: folding must apply to BOTH sides.
# --------------------------------------------------------------------------- #


def test_unaccented_query_matches_accented_text():
    assert query_score("Café Menu", "cafe") is not None


def test_accented_query_matches_unaccented_text():
    assert query_score("Cafe Menu", "café") is not None


def test_accented_and_unaccented_forms_score_identically():
    assert query_score("Café Menu", "cafe") == query_score("Cafe Menu", "cafe")
    assert query_score("Café Menu", "café") == query_score("Cafe Menu", "cafe")


def test_composed_and_decomposed_forms_score_identically():
    composed = "Caf\u00e9 Menu"  # e-acute as one code point
    decomposed = "Cafe\u0301 Menu"  # e + combining acute accent
    assert composed != decomposed
    assert query_score(composed, "cafe") == query_score(decomposed, "cafe")
    assert query_score(composed, composed) == query_score(decomposed, composed)
    assert query_score(composed, decomposed) == query_score(decomposed, decomposed)


def test_accent_folding_survives_the_whole_string_bonus():
    # Exact-match bonus must still fire once both sides are folded.
    assert query_score("Café", "cafe") == query_score("Cafe", "cafe") == 3000


def test_accented_text_is_not_truncated_at_the_accent():
    # Previously "Café" tokenised to ["caf"], so "menu"-style suffixes vanished.
    assert query_score("Résumé parser", "resume") is not None
    assert query_score("Résumé parser", "parser") is not None


# --------------------------------------------------------------------------- #
# Mixed-script text.
# --------------------------------------------------------------------------- #


def test_mixed_script_text_is_searchable_by_each_script():
    text = "Refactor 日本語 парсер"
    assert query_score(text, "refactor") is not None
    assert query_score(text, "日本語") is not None
    assert query_score(text, "парсер") is not None


def test_mixed_script_query_requires_all_tokens():
    text = "Refactor 日本語 parser module"
    assert query_score(text, "日本語 parser") is not None
    assert query_score(text, "日本語 missing") is None


# --------------------------------------------------------------------------- #
# ranked_matches ordering.
# --------------------------------------------------------------------------- #


def test_ranked_matches_orders_best_first_with_stable_index_tiebreak():
    items = [
        ("a", "parkside"),  # subsequence tier
        ("b", "parse"),  # exact tier
        ("c", "parser"),  # prefix tier
        ("d", "parse"),  # exact tier, later index
        ("e", "zzzz"),  # no match
    ]
    ranked = ranked_matches(items, "parse", lambda item: item[1])
    assert [item[0] for item in ranked] == ["b", "d", "c", "a"]


def test_ranked_matches_returns_everything_for_empty_query():
    items = ["alpha", "beta", "gamma"]
    assert ranked_matches(items, "", lambda item: item) == items


def test_ranked_matches_finds_non_ascii_items():
    items = [
        ("ascii", "Refactor parser"),
        ("cjk", "日本語のタスク"),
        ("cyrillic", "Задача один"),
        ("accented", "Café Menu"),
    ]
    text_for_item = lambda item: item[1]  # noqa: E731
    assert [item[0] for item in ranked_matches(items, "日本", text_for_item)] == ["cjk"]
    assert [item[0] for item in ranked_matches(items, "Задача", text_for_item)] == ["cyrillic"]
    assert [item[0] for item in ranked_matches(items, "cafe", text_for_item)] == ["accented"]
    assert [item[0] for item in ranked_matches(items, "Refactor", text_for_item)] == ["ascii"]
