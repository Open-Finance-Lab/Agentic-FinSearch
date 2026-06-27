"""Investigation tooling (NOT package code) — recover the benchmark generator's
concept->tag SELECTION rule empirically from cases_v1_final.json + fetched
companyfacts, by reversing the gold_fact_path hashes.

For every (case, required_fact value): find the us-gaap fact(s) in that company
reporting that value, hash each candidate (cik, tag, ps, pe, unit, accn) with the
RECONCILED recipe, and keep the one whose hash is in gold_fact_path. That match
reveals the chosen tag AND re-validates the recipe on hundreds of fresh facts.

Also captures, per matched fact, the SIBLING us-gaap tags the same company reported
for the same (period, unit, value) but the generator passed over -> pairwise
priority constraints (chosen > sibling).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # Main/backend, so `import truthlayer`
from truthlayer import store  # noqa: E402

CASES = Path("/mnt/d/fingpt/Materials/XBRL Tree/Benchmark/cases_v1_final.json")
CACHE = HERE / "companyfacts"


def _d(s):
    return s if s else None  # keep ISO string / None; make_fact_id str()s it


def load_company_index(cik: int):
    """Return list of (val, tag, ps, pe, unit, accn) for every us-gaap entry, or None."""
    path = CACHE / f"CIK{cik:010d}.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    rows = []
    for tag, body in doc.get("facts", {}).get("us-gaap", {}).items():
        for unit, entries in body.get("units", {}).items():
            for e in entries:
                rows.append((e["val"], tag, e.get("start"), e.get("end"), unit, e["accn"]))
    return rows


def concept_of(key: str) -> str:
    """Normalize a required_facts key to a concept label (strip trailing period token)."""
    return re.sub(r"_(\d{4}|y\d+|t\d*|prior|current|base|end|start)$", "", key)


def main() -> None:
    cases = json.loads(CASES.read_text())
    # ticker -> cik from single-entity cases (for cross-entity required_facts keyed by ticker)
    t2c = {}
    for c in cases:
        e = c["entity"]
        if isinstance(e, dict) and "cik" in e:
            t2c[e.get("ticker")] = e["cik"]
    # resolve the cross-entity-only tickers from the fetch's cache filenames is hard;
    # instead build ticker->cik from SEC map lazily only if needed (most resolve via t2c).

    idx_cache: dict[int, list | None] = {}

    def idx(cik):
        if cik not in idx_cache:
            idx_cache[cik] = load_company_index(cik)
        return idx_cache[cik]

    # concept -> Counter(chosen_tag)
    concept_tags: dict[str, Counter] = defaultdict(Counter)
    # (concept) -> Counter("chosen>sibling")
    priority: dict[str, Counter] = defaultdict(Counter)
    # per-(cik,concept) chosen tag, to detect per-company divergence
    company_choice: dict[tuple, set] = defaultdict(set)
    resolved = 0
    unresolved = []
    no_data = Counter()
    total_facts = 0

    for c in cases:
        gold = set(c["gold_fact_path"])
        cik_default = c["entity"].get("cik") if isinstance(c["entity"], dict) else None
        for key, val in c["required_facts"].items():
            total_facts += 1
            # resolve cik: single-entity uses default; cross-entity key is a ticker
            cik = cik_default
            concept = concept_of(key)
            if cik is None:  # cross-entity: key is a ticker
                cik = t2c.get(key)
                concept = "cross:" + c["template_id"]
            if cik is None:
                unresolved.append(("no-cik", key, c["template_id"]))
                continue
            rows = idx(cik)
            if rows is None:
                no_data[cik] += 1
                continue
            # candidate facts: same value (exact float match)
            cands = [r for r in rows if r[0] == val]
            hit = None
            for (v, tag, ps, pe, unit, accn) in cands:
                h = store.make_fact_id(cik, tag, _d(ps), _d(pe), unit, accn)
                if h in gold:
                    hit = (tag, ps, pe, unit, accn)
                    break
            if hit is None:
                unresolved.append((cik, key, val, len(cands)))
                continue
            resolved += 1
            ctag, cps, cpe, cunit, _accn = hit
            concept_tags[concept][ctag] += 1
            company_choice[(cik, concept)].add(ctag)
            # siblings: other tags in same company reporting same (period, unit, value)
            for (v, tag, ps, pe, unit, accn) in rows:
                if tag != ctag and v == val and ps == cps and pe == cpe and unit == cunit:
                    priority[concept][f"{ctag} > {tag}"] += 1

    # ---- report ----
    print("=" * 70)
    print(f"COVERAGE: {resolved}/{total_facts} required_facts resolved to a gold tag")
    print(f"  unresolved: {len(unresolved)}   companies-with-no-data hits: {sum(no_data.values())}")
    if no_data:
        print(f"  missing cik data for {len(no_data)} companies: {sorted(no_data)[:10]}...")
    print("=" * 70)
    print("\n### CONCEPT -> CHOSEN TAG DISTRIBUTION (the selection rule)")
    for concept in sorted(concept_tags):
        dist = concept_tags[concept]
        print(f"\n  {concept}  (n={sum(dist.values())})")
        for tag, n in dist.most_common():
            print(f"      {n:4d}  {tag}")
    print("\n### PRIORITY CONSTRAINTS (chosen > sibling, same period/value)")
    any_pri = False
    for concept in sorted(priority):
        if priority[concept]:
            any_pri = True
            print(f"  {concept}:")
            for rel, n in priority[concept].most_common():
                print(f"      {n:4d}  {rel}")
    if not any_pri:
        print("  (none — no company reported two value-equal candidate tags for one period)")
    print("\n### PER-COMPANY DIVERGENCE (same concept, different tag across companies)")
    div = defaultdict(set)
    for (cik, concept), tags in company_choice.items():
        div[concept] |= tags
    for concept in sorted(div):
        if len(div[concept]) > 1:
            print(f"  {concept}: {sorted(div[concept])}")
    print("\n### UNRESOLVED SAMPLES (first 25)")
    for u in unresolved[:25]:
        print("   ", u)


if __name__ == "__main__":
    main()
