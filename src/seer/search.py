"""
Query matching for alert rules -- shared by the table view's search box and
the detail view's quick switcher.

Fuzzy matching is delegated to textual.fuzzy (the engine behind Textual's
command palette) instead of a hand-rolled scorer: it ranks word-boundary and
first-letter hits higher and reports which characters matched. FuzzySearch is
used directly (rather than the higher-level Matcher) because Matcher's
highlight() returns a textual Content object, while the widgets here work
with rich Text.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.fuzzy import FuzzySearch

from seer.models import AlertRule

MATCH_STYLE = Style(bold=True, underline=True)

_fuzzy = FuzzySearch()


def searchable_fields(rule: AlertRule) -> tuple[str, str, str, str]:
    return (rule.name, rule.namespace, rule.severity, rule.group)


def filter_rules(rules: list[AlertRule], query: str, fuzzy: bool) -> list[AlertRule]:
    """Rules matching `query` in any searchable field, keeping input order."""
    query = query.strip().lower()
    if not query:
        return list(rules)
    if fuzzy:
        return [r for r in rules if any(_fuzzy.match(query, f)[0] > 0 for f in searchable_fields(r))]
    return [r for r in rules if any(query in f.lower() for f in searchable_fields(r))]


def rank_rules_by_name(rules: list[AlertRule], query: str, limit: int) -> list[tuple[int, Text]]:
    """Best `limit` fuzzy matches of `query` against rule names.

    Returns (index into `rules`, name with the matched characters highlighted)
    pairs, best match first. An empty query returns the first `limit` rules
    in their existing order.
    """
    query = query.strip()
    if not query:
        return [(i, Text(rule.name)) for i, rule in enumerate(rules[:limit])]

    scored = []
    for i, rule in enumerate(rules):
        score, offsets = _fuzzy.match(query, rule.name)
        if score > 0:
            scored.append((-score, len(rule.name), i, offsets))
    scored.sort(key=lambda entry: entry[:3])

    results = []
    for _, _, i, offsets in scored[:limit]:
        name = Text(rules[i].name)
        for offset in offsets:
            name.stylize(MATCH_STYLE, offset, offset + 1)
        results.append((i, name))
    return results
