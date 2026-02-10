import re
from collections.abc import Iterable
from typing import Any

REQUIRED_FIELDS = {"id", "type", "title", "insight", "data"}
SUPPORTED_TYPES = {
    "line",
    "bar",
    "stacked-bar",
    "grouped-bar",
    "pie",
    "area",
    "scatter",
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "show",
    "that",
    "the",
    "this",
    "to",
    "what",
    "with",
}

_TREND_KEYWORDS = {"trend", "time", "year", "monthly", "daily", "change"}
_COMPARE_KEYWORDS = {
    "compare",
    "comparison",
    "top",
    "rank",
    "ranking",
    "versus",
    "vs",
}
_SHARE_KEYWORDS = {
    "share",
    "composition",
    "breakdown",
    "proportion",
    "distribution",
    "percent",
}
_RELATIONSHIP_KEYWORDS = {
    "relationship",
    "correlation",
    "related",
    "association",
}


class SelectionDebug(dict):
    """Simple dict subclass used for typed selection debug payloads."""


def _normalize_tokens(text: str) -> set[str]:
    if not text:
        return set()
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if token not in _STOPWORDS}


def _overlap_count(query_tokens: set[str], text: str) -> int:
    if not text:
        return 0
    return len(query_tokens & _normalize_tokens(text))


def _validate_candidate(candidate: Any) -> tuple[bool, str]:
    if not isinstance(candidate, dict):
        return False, "chart_invalid_schema"

    missing = REQUIRED_FIELDS - set(candidate.keys())
    if missing:
        return False, "chart_invalid_schema"

    if candidate.get("type") not in SUPPORTED_TYPES:
        return False, "chart_unsupported"

    data = candidate.get("data")
    if not isinstance(data, list) or len(data) == 0:
        return False, "chart_invalid_schema"

    return True, "candidate_valid"


def _intent_bonus(query_tokens: set[str], chart_type: str) -> int:
    bonus = 0

    if query_tokens & _TREND_KEYWORDS and chart_type in {"line", "area"}:
        bonus += 2
    if query_tokens & _COMPARE_KEYWORDS and chart_type in {
        "bar",
        "grouped-bar",
    }:
        bonus += 2
    if query_tokens & _SHARE_KEYWORDS and chart_type in {
        "stacked-bar",
        "pie",
    }:
        bonus += 2
    if query_tokens & _RELATIONSHIP_KEYWORDS and chart_type == "scatter":
        bonus += 2

    return bonus


def _score_candidates(
    query: str,
    charts_data: Iterable[Any],
) -> tuple[dict[str, Any] | None, list[SelectionDebug]]:
    query_tokens = _normalize_tokens(query)
    valid_candidates: list[
        tuple[float, int, dict[str, Any], SelectionDebug]
    ] = []
    debug: list[SelectionDebug] = []

    for idx, candidate in enumerate(charts_data):
        is_valid, reason = _validate_candidate(candidate)

        if not is_valid:
            debug.append(
                SelectionDebug(
                    index=idx,
                    chart_id=(
                        candidate.get("id")
                        if isinstance(candidate, dict)
                        else None
                    ),
                    chart_type=(
                        candidate.get("type")
                        if isinstance(candidate, dict)
                        else None
                    ),
                    status=reason,
                )
            )
            continue

        title_overlap = _overlap_count(
            query_tokens, str(candidate.get("title", ""))
        )
        insight_overlap = _overlap_count(
            query_tokens, str(candidate.get("insight", ""))
        )
        intent = _intent_bonus(query_tokens, str(candidate.get("type")))
        main_chart_bonus = 0.5 if candidate.get("id") == "main_chart" else 0.0

        score = (
            (2 * title_overlap) + insight_overlap + intent + main_chart_bonus
        )

        candidate_debug = SelectionDebug(
            index=idx,
            chart_id=candidate.get("id"),
            chart_type=candidate.get("type"),
            status="scored",
            score=score,
            title_overlap=title_overlap,
            insight_overlap=insight_overlap,
            intent_bonus=intent,
            main_chart_bonus=main_chart_bonus,
        )
        debug.append(candidate_debug)
        valid_candidates.append((score, idx, candidate, candidate_debug))

    if not valid_candidates:
        return None, debug

    valid_candidates.sort(
        key=lambda item: (
            item[0],
            1 if item[2].get("id") == "main_chart" else 0,
            -item[1],
        ),
        reverse=True,
    )

    selected = valid_candidates[0][2]
    return selected, debug


def select_best_chart(query: str, charts_data: list[dict]) -> dict | None:
    selected, _ = _score_candidates(query=query, charts_data=charts_data)
    return selected


def select_best_chart_with_debug(
    query: str,
    charts_data: list[dict],
) -> tuple[dict | None, list[SelectionDebug]]:
    return _score_candidates(query=query, charts_data=charts_data)
