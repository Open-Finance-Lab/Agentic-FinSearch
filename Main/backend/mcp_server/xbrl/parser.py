"""iXBRL parser for SEC filing verification.

Parses Inline XBRL (iXBRL) HTML/XML files to extract tagged financial facts.
Uses only Python stdlib (xml.etree.ElementTree). No external dependencies.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

NAMESPACES = {
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
}

# Company name/ticker -> filename prefix mapping for common lookups
COMPANY_ALIASES = {
    "apple": "aapl",
    "microsoft": "msft",
    "tesla": "tsla",
}

# Cache parsed filings: filepath -> list of facts
_cache: Dict[str, List[Dict]] = {}


def _parse_contexts(root: ET.Element) -> Dict[str, Dict]:
    """Extract context definitions: id -> {period_start, period_end, has_dimensions}."""
    contexts = {}
    for ctx in root.findall(".//xbrli:context", NAMESPACES):
        cid = ctx.get("id")
        if not cid:
            continue

        period = ctx.find(".//xbrli:period", NAMESPACES)
        if period is None:
            continue

        start_el = period.find("xbrli:startDate", NAMESPACES)
        end_el = period.find("xbrli:endDate", NAMESPACES)
        instant_el = period.find("xbrli:instant", NAMESPACES)

        segment = ctx.find(".//xbrli:segment", NAMESPACES)
        has_dimensions = segment is not None and len(segment) > 0

        if start_el is not None and end_el is not None:
            contexts[cid] = {
                "period_start": start_el.text,
                "period_end": end_el.text,
                "has_dimensions": has_dimensions,
            }
        elif instant_el is not None:
            contexts[cid] = {
                "period_start": None,
                "period_end": instant_el.text,
                "has_dimensions": has_dimensions,
            }

    return contexts


def _parse_units(root: ET.Element) -> Dict[str, str]:
    """Extract unit definitions: id -> human-readable label."""
    units = {}
    for unit in root.findall(".//xbrli:unit", NAMESPACES):
        uid = unit.get("id")
        if not uid:
            continue
        measure = unit.find("xbrli:measure", NAMESPACES)
        if measure is not None and measure.text:
            # "iso4217:USD" -> "USD", "xbrli:pure" -> "pure", "xbrli:shares" -> "shares"
            units[uid] = measure.text.split(":")[-1]
        else:
            # Compound unit (e.g., usdPerShare = USD/shares)
            units[uid] = uid
    return units


def _clean_numeric_text(text: str) -> Optional[float]:
    """Parse display text to a float, handling commas, em-dashes, parentheses."""
    if not text:
        return None
    text = text.strip()
    # Em-dash or long dash = zero
    if text in ("\u2014", "\u2013", "-", ""):
        return 0.0
    # Remove commas and spaces
    text = text.replace(",", "").replace(" ", "")
    # Handle parentheses for negatives: (123) -> -123
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def parse_filing(filepath: Path) -> List[Dict]:
    """Parse an iXBRL file and return all numeric facts.

    Returns list of dicts with keys:
        tag: str - local tag name (e.g., "RevenueFromContractWithCustomerExcludingAssessedTax")
        namespace: str - namespace prefix (e.g., "us-gaap")
        value: float - computed value (display_text * 10^scale)
        unit: str - unit label (e.g., "USD", "pure", "shares")
        period_start: str|None - start date for duration contexts, None for instants
        period_end: str - end date (or instant date)
        has_dimensions: bool - True if context has dimensional breakdowns
    """
    cache_key = str(filepath)
    if cache_key in _cache:
        return _cache[cache_key]

    tree = ET.parse(filepath)
    root = tree.getroot()

    contexts = _parse_contexts(root)
    units = _parse_units(root)

    # Find all ix:nonFraction elements
    all_fractions = root.findall(".//ix:nonFraction", NAMESPACES)

    # Identify parent elements (those that contain child ix:nonFraction)
    parents = set()
    for elem in all_fractions:
        children = elem.findall(".//ix:nonFraction", NAMESPACES)
        if children:
            parents.add(elem)

    # Process only leaf nodes, deduplicate by (name, contextRef)
    seen = set()
    facts = []

    for elem in all_fractions:
        if elem in parents:
            continue

        name = elem.get("name", "")
        ctx_ref = elem.get("contextRef", "")
        dedup_key = (name, ctx_ref)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Parse namespace:tag
        if ":" in name:
            ns_prefix, local_tag = name.split(":", 1)
        else:
            ns_prefix, local_tag = "", name

        # Get display text (all text content, ignoring child element tags)
        raw_text = ET.tostring(elem, encoding="unicode", method="text").strip()
        parsed_num = _clean_numeric_text(raw_text)
        if parsed_num is None:
            continue

        # Apply scale
        scale = int(elem.get("scale", "0"))
        value = parsed_num * (10 ** scale)

        # Apply sign attribute
        if elem.get("sign") == "-":
            value = -abs(value)

        # Resolve context
        ctx = contexts.get(ctx_ref, {})
        if not ctx:
            continue

        # Resolve unit
        unit_ref = elem.get("unitRef", "")
        unit_label = units.get(unit_ref, unit_ref)

        facts.append({
            "tag": local_tag,
            "namespace": ns_prefix,
            "value": value,
            "unit": unit_label,
            "period_start": ctx.get("period_start"),
            "period_end": ctx.get("period_end", ""),
            "has_dimensions": ctx.get("has_dimensions", False),
        })

    _cache[cache_key] = facts
    return facts


def find_filing(company: str, filings_dir: Path) -> Optional[Path]:
    """Resolve a company name or ticker to a filing path.

    Checks ticker match first, then alias map, against XML files in filings_dir.
    """
    company_lower = company.lower().strip()

    # Resolve alias to ticker
    ticker = COMPANY_ALIASES.get(company_lower, company_lower)

    # Scan filings directory for a matching file
    for f in filings_dir.glob("*.xml"):
        if f.name.lower().startswith(ticker):
            return f

    return None


def query_facts(
    company: str,
    tag_name: str,
    filings_dir: Path,
) -> List[Dict]:
    """Query facts for a company and tag name.

    Args:
        company: Company name or ticker (e.g., "apple", "AAPL")
        tag_name: XBRL tag local name (e.g., "EffectiveIncomeTaxRateContinuingOperations")
        filings_dir: Path to directory containing iXBRL files

    Returns:
        List of matching facts sorted by period_end descending (most recent first).
        Empty list if company or tag not found.
    """
    filepath = find_filing(company, filings_dir)
    if filepath is None:
        return []

    facts = parse_filing(filepath)
    matches = [f for f in facts if f["tag"] == tag_name]

    # Sort by period_end descending (most recent first)
    matches.sort(key=lambda f: f["period_end"] or "", reverse=True)
    return matches
