from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from difflib import SequenceMatcher
from typing import TypeVar


T = TypeVar("T")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def contains_query(text: str, query: str) -> bool:
    return query_score(text, query) is not None


def ranked_matches(items: Iterable[T], query: str, text_for_item: Callable[[T], str]) -> list[T]:
    scored: list[tuple[int, int, T]] = []
    for index, item in enumerate(items):
        score = query_score(text_for_item(item), query)
        if score is not None:
            scored.append((score, index, item))
    return [item for _, _, item in sorted(scored, key=lambda match: (-match[0], match[1]))]


def query_score(text: str, query: str) -> int | None:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return 0
    normalized_text = _normalize_text(text)
    if not normalized_text:
        return None

    query_tokens = _tokens(normalized_query)
    text_tokens = _tokens(normalized_text)
    if not query_tokens or not text_tokens:
        return None

    token_scores: list[int] = []
    for query_token in query_tokens:
        token_score = _best_token_score(query_token, text_tokens)
        if token_score is None:
            return None
        token_scores.append(token_score)

    score = sum(token_scores)
    padded_text = f" {normalized_text} "
    padded_query = f" {normalized_query} "
    if normalized_query == normalized_text:
        score += 2000
    elif padded_query in padded_text:
        score += 1200
    elif normalized_query in normalized_text:
        score += 700
    return score


def _best_token_score(query_token: str, text_tokens: list[str]) -> int | None:
    scores = [_token_score(query_token, text_token) for text_token in text_tokens]
    matches = [score for score in scores if score is not None]
    return max(matches) if matches else None


def _token_score(query_token: str, text_token: str) -> int | None:
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


def _is_ordered_subsequence(needle: str, haystack: str) -> bool:
    position = 0
    for character in needle:
        position = haystack.find(character, position)
        if position == -1:
            return False
        position += 1
    return True


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(value.casefold())
