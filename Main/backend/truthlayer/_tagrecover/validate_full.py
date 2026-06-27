"""Exhaustive validation: for EVERY single-entity required_fact whose gold hash we
can reverse, confirm the reconciled CONCEPT_REGISTRY (first-present-tag-wins over the
tags the company actually reports for the gold fact's period) selects the SAME tag
the generator stored. Goal: 0 mismatches across all concepts."""
from __future__ import annotations
import json, re, sys
from collections import Counter
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from truthlayer import store, registry  # noqa
CACHE = HERE / "companyfacts"
cases = json.loads(Path("/mnt/d/fingpt/Materials/XBRL Tree/Benchmark/cases_v1_final.json").read_text())


def load(cik):
    p = CACHE / f"CIK{cik:010d}.json"
    return json.loads(p.read_text()) if p.exists() else None


def concept_of(key):
    # Period tokens always carry a number (t1/t2, y1, 2025); t\d+ not t\d* so a
    # concept legitimately ending in a bare '_t' is not over-stripped.
    return re.sub(r"_(\d{4}|y\d+|t\d+|prior|current|base|end|start)$", "", key)


def tags_present_at(doc, ps, pe, unit):
    """All us-gaap tags reporting a fact at exactly (period_start, period_end) in the
    gold fact's unit. NOTE: must use the gold unit, not hardcode USD — the benchmark
    includes foreign private issuers (ASML/EUR, TM/JPY, ENB/CAD) whose headline facts
    are in the home currency; _select prefers USD but does not exclude other units."""
    present = set()
    for tag, body in doc["facts"]["us-gaap"].items():
        for u, ents in body.get("units", {}).items():
            if u != unit:
                continue
            for e in ents:
                if e.get("start") == ps and e.get("end") == pe:
                    present.add(tag)
    return present


checked = matches = 0
mismatches = []
no_concept = Counter()
for c in cases:
    e = c["entity"]
    if not (isinstance(e, dict) and "cik" in e):
        continue  # cross-entity uses identical concepts/tags -> covered transitively
    cik = e["cik"]
    gold = set(c["gold_fact_path"])
    doc = load(cik)
    if not doc:
        continue
    for key, val in c["required_facts"].items():
        bench_concept = concept_of(key)
        reg_concept = registry.resolve_benchmark_concept(bench_concept)
        if reg_concept not in registry.CONCEPT_REGISTRY:
            no_concept[bench_concept] += 1
            continue
        # find the gold fact for this value: tag + period whose hash is in gold
        goldfact = None
        for tag, body in doc["facts"]["us-gaap"].items():
            for unit, ents in body.get("units", {}).items():
                for en in ents:
                    if en["val"] == val and store.make_fact_id(
                            cik, tag, en.get("start"), en.get("end"), unit, en["accn"]) in gold:
                        goldfact = (tag, en.get("start"), en.get("end"), unit)
        if goldfact is None:
            continue  # ratio/derived/drifted — not a resolvable raw fact
        gold_tag, ps, pe, gold_unit = goldfact
        present = tags_present_at(doc, ps, pe, gold_unit)
        spec = registry.CONCEPT_REGISTRY[reg_concept]
        selected = next((t for t in spec.tags if t in present), None)
        checked += 1
        if selected == gold_tag:
            matches += 1
        else:
            mismatches.append((e.get("ticker"), bench_concept, "gold=", gold_tag,
                               "registry=", selected, "present∩regtags=",
                               [t for t in spec.tags if t in present]))

print(f"CHECKED {checked} resolvable single-entity facts")
print(f"MATCHES {matches}  MISMATCHES {len(mismatches)}")
if no_concept:
    print(f"concepts with no registry mapping (expected: derived ratios): {dict(no_concept)}")
for m in mismatches[:30]:
    print("  MISMATCH", m)
print("RESULT:", "PASS — registry reproduces gold tag selection everywhere"
      if not mismatches else "FAIL — see mismatches above")
