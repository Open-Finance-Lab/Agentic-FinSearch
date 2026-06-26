from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import requests

from truthlayer import store

CF_DIR = store.DATA_DIR / "companyfacts"
USER_AGENT = "Agentic FinSearch admin@agenticfinsearch.org"

# Demo trio. CIK is the key; the API path is zero-padded to 10 digits.
DEMO_CIKS = {"AAPL": 320193, "MSFT": 789019, "TSLA": 1318605}

# Keep vendored snapshots small: prune to the tags the registry actually uses.
from truthlayer.registry import CONCEPT_REGISTRY  # noqa: E402
_KEEP_TAGS = {t for spec in CONCEPT_REGISTRY.values() for t in spec.tags}


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def companyfacts_rows(doc: dict):
    """Yield row tuples in store.FACT_COLUMNS order for every fact in the doc."""
    cik = doc["cik"]
    for taxonomy, concepts in doc.get("facts", {}).items():
        for tag, body in concepts.items():
            for unit, entries in body.get("units", {}).items():
                for e in entries:
                    ps, pe = _d(e.get("start")), _d(e.get("end"))
                    val = e["val"]
                    yield (
                        store.make_fact_id(cik, taxonomy, tag, unit, ps, pe, e["accn"]),
                        cik, taxonomy, tag, unit, float(val), Decimal(str(val)),
                        ps, pe, e.get("fy"), e.get("fp"), e.get("form"),
                        e["accn"], _d(e.get("filed")), e.get("frame"),
                    )


def ingest_doc(con, doc: dict) -> None:
    rows = list(companyfacts_rows(doc))
    # Fail loud on an intra-document fact_id collision: two entries the canonical tuple
    # (cik,taxonomy,tag,unit,period_start,period_end,accession) cannot distinguish but
    # that differ in fy/fp/frame/value (e.g. one accession tagging a period both
    # FY/CYxxxx and Q4/CYxxxxQ4). DuckDB ON CONFLICT DO NOTHING would SILENTLY keep
    # whichever came first in source order — non-deterministic data loss. The vendored
    # snapshots have zero such collisions; if a new one trips this, the fact_id recipe
    # must fold the distinguishing field (the S2/S13 reconcile checkpoint).
    seen: dict[str, tuple] = {}
    for row in rows:
        fid = row[0]
        if fid in seen and seen[fid] != row:
            raise ValueError(
                f"intra-document fact_id collision for {fid!r}: two distinct facts share "
                f"the canonical tuple. Fold the distinguishing field into make_fact_id "
                f"(spec S2/S13). cik={doc.get('cik')} tag={row[3]!r}"
            )
        seen[fid] = row
    placeholders = ",".join(["?"] * len(store.FACT_COLUMNS))
    con.executemany(
        f"INSERT INTO facts VALUES ({placeholders}) ON CONFLICT (fact_id) DO NOTHING", rows
    )
    con.execute(
        "INSERT INTO entities VALUES (?, ?, ?) ON CONFLICT (cik) DO NOTHING",
        [doc["cik"], None, doc.get("entityName")],
    )


# --- snapshot building (manual, network) -----------------------------------

def fetch_companyfacts(cik: int) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _prune(doc: dict) -> dict:
    """Keep only the us-gaap tags the registry references; drop everything else
    so the committed snapshot is ~tens of KB instead of multiple MB. Returns a new
    dict — does not mutate the caller's doc."""
    gaap = doc.get("facts", {}).get("us-gaap", {})
    kept = {t: gaap[t] for t in _KEEP_TAGS if t in gaap}
    return {**doc, "facts": {"us-gaap": kept}}


def save_snapshots() -> None:
    """One-time, requires network. Writes pruned companyfacts JSON for the demo trio."""
    CF_DIR.mkdir(parents=True, exist_ok=True)
    for ticker, cik in DEMO_CIKS.items():
        doc = _prune(fetch_companyfacts(cik))
        (CF_DIR / f"CIK{cik:010d}.json").write_text(json.dumps(doc))
        print(f"saved {ticker} CIK{cik:010d}.json")


def build_from_vendored(db_path=store.DB_PATH):
    """Build the DuckDB store from the committed snapshots (offline, deterministic)."""
    con = store.connect(db_path)
    ticker_by_cik = {cik: tk for tk, cik in DEMO_CIKS.items()}
    for path in sorted(CF_DIR.glob("CIK*.json")):
        doc = json.loads(path.read_text())
        ingest_doc(con, doc)
        con.execute("UPDATE entities SET ticker = ? WHERE cik = ?",
                    [ticker_by_cik.get(doc["cik"]), doc["cik"]])
    return con
