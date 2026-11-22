from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import filterfalse
from typing import TypeVar


T = TypeVar("T")
# Word characters minus underscore, Unicode-aware.  The previous ``[a-z0-9]+``
# pattern dropped every non-ASCII script, which made search silently return
# nothing for CJK, Cyrillic, Greek, Hebrew, ... and truncated accented Latin
# ("café" tokenised to "caf").
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

_EXACT_TOKEN_SCORE = 1000
_FUZZY_TOKEN_SCORE = 500
_FUZZY_RATIO = 0.78

# Bounds the shared token pool below. Past this the pool is simply emptied:
# already-prepared texts keep their strings, new ones start sharing afresh.
_TOKEN_POOL_LIMIT = 100_000
_TOKEN_POOL: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class SearchText:
    """Text that has been normalised and tokenised once, ready to score.

    Normalising and tokenising a task's full Markdown costs far more than
    scoring it, so a caller whose items outlive a single query (a cached
    repository scan) builds this once per item with :func:`prepare_search_text`
    and hands it back from ``text_for_item`` instead of the raw string.
    """

    normalized: str
    tokens: frozenset[str]


def prepare_search_text(text: str) -> SearchText:
    """Build a reusable :class:`SearchText`; scores are identical to passing ``text``."""
    return _prepare(text, pooled=True)


def contains_query(text: str | SearchText, query: str) -> bool:
    return query_score(text, query) is not None


def ranked_matches(
    items: Iterable[T],
    query: str,
    text_for_item: Callable[[T], str | SearchText],
) -> list[T]:
    matcher = _QueryMatcher(query)
    if not matcher.normalized:
        # An empty query scores every item 0, so the input order is the ranking.
        return list(items)
    scored: list[tuple[int, T]] = []
    for item in items:
        score = matcher.score(_as_search_text(text_for_item(item)))
        if score is not None:
            scored.append((score, item))
    # list.sort is stable, so equal scores keep their input order.
    scored.sort(key=lambda match: -match[0])
    return [item for _, item in scored]


def query_score(text: str | SearchText, query: str) -> int | None:
    matcher = _QueryMatcher(query)
    if not matcher.normalized:
        return 0
    return matcher.score(_as_search_text(text))


class _QueryMatcher:
    """One query, scored against any number of texts.

    A query token's score against a text token depends on that pair alone, and
    a project's texts share most of their vocabulary. So each distinct text
    token is scored once per query token and remembered, instead of once per
    occurrence in every text; that is what keeps the fuzzy tier (a
    ``SequenceMatcher`` per pair) affordable across thousands of tasks.
    """

    __slots__ = ("normalized", "_boundary_prefix", "_boundary_suffix", "_boundary_infix", "_token_scorers")

    def __init__(self, query: str) -> None:
        self.normalized = _normalize_text(query)
        self._boundary_prefix = f"{self.normalized} "
        self._boundary_suffix = f" {self.normalized}"
        self._boundary_infix = f" {self.normalized} "
        scorers: dict[str, _TokenScorer] = {}
        # A repeated query token counts once per repetition, but shares a scorer.
        self._token_scorers = [
            scorers.setdefault(token, _TokenScorer(token)) for token in _TOKEN_RE.findall(self.normalized)
        ]

    def score(self, text: SearchText) -> int | None:
        """Score ``text``; the caller handles the empty-query case first."""
        if not text.normalized or not self._token_scorers or not text.tokens:
            return None
        score = 0
        for scorer in self._token_scorers:
            token_score = scorer.best(text.tokens)
            if token_score is None:
                return None
            score += token_score
        return score + self._phrase_bonus(text.normalized)

    def _phrase_bonus(self, normalized_text: str) -> int:
        query = self.normalized
        if query not in normalized_text:
            return 0
        if query == normalized_text:
            return 2000
        # Both sides are single-spaced with no outer whitespace, so the query
        # sits on word boundaries exactly when one of these holds.
        if (
            normalized_text.startswith(self._boundary_prefix)
            or normalized_text.endswith(self._boundary_suffix)
            or self._boundary_infix in normalized_text
        ):
            return 1200
        return 700


class _TokenScorer:
    """Best score of one query token over a text's tokens, memoised per text token."""

    __slots__ = ("_token", "_length", "_seen", "_hits", "_sequence_matcher")

    def __init__(self, token: str) -> None:
        self._token = token
        self._length = len(token)
        self._seen: set[str] = set()
        self._hits: dict[str, int] = {}
        self._sequence_matcher: SequenceMatcher[str] | None = None

    def best(self, text_tokens: frozenset[str]) -> int | None:
        if self._token in text_tokens:
            return _EXACT_TOKEN_SCORE
        unseen = text_tokens - self._seen
        if unseen:
            hits = self._hits
            for text_token in unseen:
                token_score = self._score(text_token)
                if token_score is not None:
                    hits[text_token] = token_score
            self._seen |= unseen
        matched = self._hits.keys() & text_tokens
        if not matched:
            return None
        return max(map(self._hits.__getitem__, matched))

    def _score(self, text_token: str) -> int | None:
        query_token = self._token
        length = self._length
        text_length = len(text_token)
        if text_length >= length:
            if query_token == text_token:
                return _EXACT_TOKEN_SCORE
            if text_token.startswith(query_token):
                return 900 - min(text_length - length, 100)
            position = text_token.find(query_token)
            if position != -1:
                return 800 - min(position, 100)
            if length >= 3 and _is_ordered_subsequence(query_token, text_token):
                return 650 - min(text_length - length, 100)
        if length >= 4 and self._is_fuzzy_match(text_token, text_length):
            return _FUZZY_TOKEN_SCORE
        return None

    def _is_fuzzy_match(self, text_token: str, text_length: int) -> bool:
        """``SequenceMatcher(None, query_token, text_token).ratio() >= 0.78``, without the waste.

        ``ratio()`` is ``2.0 * matches / (len(a) + len(b))`` and ``matches`` can
        never exceed the shorter length, so most pairs are ruled out by length
        alone. ``quick_ratio()`` is difflib's own cheaper upper bound. Both are
        the filters ``difflib.get_close_matches`` uses, and neither can change
        the verdict.
        """
        length = self._length
        if 2.0 * min(length, text_length) / (length + text_length) < _FUZZY_RATIO:
            return False
        matcher = self._sequence_matcher
        if matcher is None:
            matcher = self._sequence_matcher = SequenceMatcher(None, self._token, "")
        matcher.set_seq2(text_token)
        return matcher.quick_ratio() >= _FUZZY_RATIO and matcher.ratio() >= _FUZZY_RATIO


def _is_ordered_subsequence(needle: str, haystack: str) -> bool:
    position = 0
    for character in needle:
        position = haystack.find(character, position)
        if position == -1:
            return False
        position += 1
    return True


def _as_search_text(text: str | SearchText) -> SearchText:
    if isinstance(text, SearchText):
        return text
    return _prepare(text, pooled=False)


def _prepare(text: str, *, pooled: bool) -> SearchText:
    normalized = _normalize_text(text)
    tokens = set(_tokens_of_normalized(normalized))
    if not pooled:
        return SearchText(normalized, frozenset(tokens))
    # Long-lived texts share one string per distinct token. Without this every
    # task would hold a private copy of the whole project vocabulary.
    if len(_TOKEN_POOL) > _TOKEN_POOL_LIMIT:
        _TOKEN_POOL.clear()
    pool = _TOKEN_POOL.setdefault
    return SearchText(normalized, frozenset(pool(token, token) for token in tokens))


def _normalize_text(value: str) -> str:
    """Case-fold, accent-fold and collapse whitespace.

    Applied to both sides of a comparison so ``cafe`` matches ``Café`` and vice
    versa.  NFKD additionally folds compatibility forms, which is what makes
    half-width katakana match full-width katakana.
    """
    return " ".join(_fold(value).split())


def _fold(value: str) -> str:
    folded = value.casefold()
    if folded.isascii():
        # Fast path: ASCII is already NFKD-normalised and carries no combining
        # marks, so the expensive work below would be a no-op.
        return folded
    decomposed = unicodedata.normalize("NFKD", folded)
    stripped = "".join(filterfalse(unicodedata.combining, decomposed))
    # Recompose so scripts that decompose into non-mark components (Hangul
    # syllables into jamo) keep their original length: several score tiers are
    # length-sensitive.
    return unicodedata.normalize("NFC", stripped)


def _tokens_of_normalized(value: str) -> list[str]:
    """Split text that has already been through :func:`_normalize_text`.

    Ideographic scripts have no word separators, so a whole CJK run becomes a
    single token and the prefix/substring tiers of ``_TokenScorer`` do the
    matching work.
    """
    return _TOKEN_RE.findall(value)
