"""Investigation tooling (NOT package code) — fetch full us-gaap companyfacts for
every company referenced by the benchmark, so the tag-selection recovery harness
can see competitor tags the vendored *pruned* snapshots threw away.

Resumable (skips cached), rate-limited (~8 req/s), us-gaap-only prune (keeps ALL
us-gaap tags — correctness over disk; recovery's coverage report catches misses).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CASES = Path("/mnt/d/fingpt/Materials/XBRL Tree/Benchmark/cases_v1_final.json")
CACHE = HERE / "companyfacts"
UA = "Agentic-FinSearch felixflyingt@gmail.com"
HEADERS = {"User-Agent": UA}


def unique_ciks() -> dict[int, str]:
    """cik -> a label (ticker if known). Covers single-entity cases + resolves the
    cross-entity comparison tickers that never appear as a primary entity."""
    cases = json.loads(CASES.read_text())
    cik_label: dict[int, str] = {}
    t2c: dict[str, int] = {}
    cross: set[str] = set()
    for c in cases:
        e = c["entity"]
        if isinstance(e, dict) and "cik" in e:
            cik_label[e["cik"]] = e.get("ticker", str(e["cik"]))
            t2c[e.get("ticker", "")] = e["cik"]
        elif isinstance(e, dict) and "companies" in e:
            cross.update(e["companies"])
    missing = sorted(cross - set(t2c))
    if missing:
        tmap = requests.get(
            "https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30
        ).json()
        by_ticker = {row["ticker"].upper(): row["cik_str"] for row in tmap.values()}
        for tk in missing:
            cik = by_ticker.get(tk.upper())
            if cik is not None:
                cik_label[int(cik)] = tk
            else:
                print(f"  ! could not resolve ticker {tk} -> cik", file=sys.stderr)
    return cik_label


def prune_usgaap(doc: dict) -> dict:
    """Keep dei? no — only us-gaap, all tags. Drops other taxonomies to shrink disk."""
    gaap = doc.get("facts", {}).get("us-gaap", {})
    return {
        "cik": doc.get("cik"),
        "entityName": doc.get("entityName"),
        "facts": {"us-gaap": gaap},
    }


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    targets = unique_ciks()
    print(f"targets: {len(targets)} companies")
    done = skipped = failed = 0
    for i, (cik, label) in enumerate(sorted(targets.items()), 1):
        out = CACHE / f"CIK{cik:010d}.json"
        if out.exists():
            skipped += 1
            continue
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        for attempt in range(3):
            try:
                r = requests.get(url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                out.write_text(json.dumps(prune_usgaap(r.json())))
                done += 1
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    failed += 1
                    print(f"  ! FAILED {label} CIK{cik:010d}: {exc}", file=sys.stderr)
                else:
                    time.sleep(1.5)
        if i % 20 == 0:
            print(f"  [{i}/{len(targets)}] done={done} skipped={skipped} failed={failed}")
        time.sleep(0.13)  # ~8 req/s, under SEC's 10/s ceiling
    print(f"DONE: fetched={done} skipped={skipped} failed={failed} total={len(targets)}")


if __name__ == "__main__":
    main()
