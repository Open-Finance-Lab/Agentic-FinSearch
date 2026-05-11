"""XBRL taxonomy search engine with PascalCase-aware keyword matching."""

import json
import re
from pathlib import Path
from typing import List, Dict


_TAXONOMY_PATH = Path(__file__).parent / "us_gaap_2026.json"
_elements: List[Dict] = []
_search_index: Dict[str, List[int]] = {}  # keyword -> list of element indices


def _split_pascal(name: str) -> List[str]:
    """Split PascalCase name into lowercase keywords.

    'DebtInstrumentFaceAmount' -> ['debt', 'instrument', 'face', 'amount']
    'EffectiveIncomeTaxRateContinuingOperations' -> ['effective', 'income', 'tax', 'rate', 'continuing', 'operations']
    """
    words = re.findall(r'[A-Z][a-z0-9]*|[0-9]+', name)
    return [w.lower() for w in words]


def _load_taxonomy():
    """Load taxonomy JSON and build search index."""
    global _elements, _search_index
    if _elements:
        return

    with open(_TAXONOMY_PATH, "r") as f:
        _elements = json.load(f)

    # Build inverted index: keyword -> set of element indices
    # Use sets to avoid double-counting when a keyword appears multiple
    # times in a PascalCase name (e.g., "IncomeTaxRate...IncomeTaxRate...")
    for idx, elem in enumerate(_elements):
        keywords = set(_split_pascal(elem["name"]))
        for kw in keywords:
            if kw not in _search_index:
                _search_index[kw] = set()
            _search_index[kw].add(idx)


def search_tags(query: str, top_k: int = 10, type_filter: str = None) -> List[Dict]:
    """Search XBRL tags by natural language query.

    Scoring: count of query keywords that match PascalCase-split tag name words.
    Ties broken by shorter names (more specific tags rank higher).

    Args:
        query: Natural language description (e.g., "effective tax rate percent")
        top_k: Number of results to return
        type_filter: Optional filter by type (e.g., "monetary", "percent")

    Returns:
        List of {name, type, period, score} dicts, highest score first.
    """
    _load_taxonomy()

    # Tokenize query into keywords
    query_keywords = set(re.findall(r'[a-zA-Z]+', query.lower()))
    if not query_keywords:
        return []

    # Score each element by keyword overlap
    scores: Dict[int, int] = {}
    for kw in query_keywords:
        # Exact keyword match
        if kw in _search_index:
            for idx in _search_index[kw]:
                scores[idx] = scores.get(idx, 0) + 1

    if not scores:
        return []

    # Build results
    results = []
    for idx, score in scores.items():
        elem = _elements[idx]
        if type_filter and elem["type"] != type_filter:
            continue
        tag_keywords = set(_split_pascal(elem["name"]))
        # Bonus: what fraction of the tag's own keywords are covered by query
        coverage = score / len(tag_keywords) if tag_keywords else 0
        results.append({
            "name": elem["name"],
            "type": elem["type"],
            "period": elem["period"],
            "score": score,
            "coverage": round(coverage, 3),
        })

    # Sort by: score desc, then coverage desc, then shorter name (more specific)
    results.sort(key=lambda r: (-r["score"], -r["coverage"], len(r["name"])))
    return results[:top_k]


def validate_tag(tag_name: str) -> bool:
    """Check if a tag name exists in the taxonomy."""
    _load_taxonomy()
    return any(e["name"] == tag_name for e in _elements)


def get_tag_info(tag_name: str) -> Dict | None:
    """Get full info for a specific tag name."""
    _load_taxonomy()
    for e in _elements:
        if e["name"] == tag_name:
            return e
    return None
