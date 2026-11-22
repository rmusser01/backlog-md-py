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

from difflib import SequenceMatcher

import pytest

from backlog_py.search.simple import (
    _is_ordered_subsequence,
    _TokenScorer,
    contains_query,
    prepare_search_text,
    query_score,
    ranked_matches,
)


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


# --------------------------------------------------------------------------- #
# Prepared text and per-query memoisation: speed-ups that must not move a score.
# --------------------------------------------------------------------------- #


def _reference_token_score(query_token: str, text_token: str) -> int | None:
    """The scoring tiers as originally written, one pair at a time, no shortcuts."""
    if query_token == text_token:
        return 1000
    if text_token.startswith(query_token):
        return 900 - min(len(text_token) - len(query_token), 100)
    if query_token in text_token:
        return 800 - min(text_token.index(query_token), 100)
    if len(query_token) >= 3 and _is_ordered_subsequence(query_token, text_token):
        return 650 - min(len(text_token) - len(query_token), 100)
    if len(query_token) >= 4 and SequenceMatcher(None, query_token, text_token).ratio() >= 0.78:
        return 500
    return None


_SCORING_SAMPLES = (
    "parse parser reparse parkside parsr sparse prase pares parsed parsing parses par pa p "
    "fingerprint fingerprnt fingerprints finger print footprint blueprint "
    "abcd abdc abce bcda abcde xabcd abcdabcd aaaa aaab abab baba "
    "task tasks 1500 150 15 日本語 日本 タスク задача задачи cafe"
).split()


def test_token_scores_match_the_unoptimised_tiers_for_every_sample_pair():
    for query_token in _SCORING_SAMPLES:
        for text_token in _SCORING_SAMPLES:
            expected = _reference_token_score(query_token, text_token)
            assert _TokenScorer(query_token).best(frozenset([text_token])) == expected, (query_token, text_token)


def test_token_scorer_keeps_verdicts_separate_across_texts():
    # The scorer remembers each text token it has judged; a match found in one
    # text must not be credited to a later text that lacks that token.
    scorer = _TokenScorer("parse")
    assert scorer.best(frozenset(["parser", "tree"])) == 899
    assert scorer.best(frozenset(["tree", "zzzz"])) is None
    assert scorer.best(frozenset(["reparse", "tree"])) == 798
    assert scorer.best(frozenset(["parser", "reparse", "parsr"])) == 899
    assert scorer.best(frozenset(["parsr"])) == 500


def test_prepared_text_scores_identically_to_raw_text():
    text = "Refactor the Résumé parser\n  for 日本語のタスク — foo_bar-baz"
    prepared = prepare_search_text(text)
    for query in ["parser", "pars", "resume parser", "タスク", "refactor the resume", "bar baz", "zzzz", "", "!!!"]:
        assert query_score(prepared, query) == query_score(text, query), query
        assert contains_query(prepared, query) == contains_query(text, query), query


def test_ranked_matches_accepts_prepared_text():
    items = [("a", "parkside"), ("b", "parse"), ("c", "parser"), ("d", "parse"), ("e", "zzzz")]
    ranked = ranked_matches(items, "parse", lambda item: prepare_search_text(item[1]))
    assert [item[0] for item in ranked] == ["b", "d", "c", "a"]


def test_repeated_query_token_counts_once_per_repetition():
    assert query_score("parse tree", "parse parse") == 2 * 1000


@pytest.mark.parametrize(
    ("text", "query", "bonus"),
    [
        ("parse", "parse", 2000),  # whole text
        ("parse tree", "parse", 1200),  # leading word
        ("syntax parse", "parse", 1200),  # trailing word
        ("a parse tree", "parse", 1200),  # interior word
        ("a parse tree", "parse tree", 1200),  # trailing phrase
        ("reparse tree", "parse", 700),  # inside a word only
        ("parse the tree", "parse tree", 0),  # tokens match, phrase does not
    ],
)
def test_phrase_bonus_tiers(text, query, bonus):
    token_total = sum(
        max(score for token in text.split() if (score := _reference_token_score(query_token, token)) is not None)
        for query_token in query.split()
    )
    assert query_score(text, query) == token_total + bonus
