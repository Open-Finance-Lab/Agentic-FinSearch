from __future__ import annotations

from collections.abc import Sequence

from truthlayer import registry, store
from truthlayer.contracts import Evidence, Period, Provenance, Query

_con = None


def _conn():
    """Lazily open the persistent store, building it from vendored snapshots
    on first use so a fresh checkout works offline."""
    global _con
    if _con is None:
        if not store.DB_PATH.exists():
            from truthlayer import ingest
            ingest.build_from_vendored()
        _con = store.connect(store.DB_PATH)
    return _con


def _resolve_cik(con, entity: str) -> int | None:
    # Ticker-first, then fall back to an all-digit CIK: a numeric ticker (e.g. non-US
    # exchange codes) must not be misread as a CIK. .strip() so entity strings from CSV
    # cells / LLM tool-call args with stray whitespace still resolve instead of silently
    # missing — _resolve_cik is the single choke point for all four read functions.
    entity = str(entity).strip()
    row = con.execute(
        "SELECT cik FROM entities WHERE upper(ticker) = upper(?)", [entity]).fetchone()
    if row:
        return row[0]
    return int(entity) if entity.isdigit() else None


def _select(con, cik: int, tag: str, period_type: str, period: Period, as_of):
    where = ["cik = ?", "taxonomy = 'us-gaap'", "tag = ?"]
    params: list = [cik, tag]
    where.append("period_start IS NULL" if period_type == "instant" else "period_start IS NOT NULL")
    if period.period_end is not None:                      # demo path
        where.append("period_end = ?"); params.append(period.period_end)
    else:                                                  # benchmark path (later)
        where.append("fy = ? AND fp = ?"); params += [period.fiscal_year, period.fiscal_period]
    if as_of is not None:
        where.append("filed <= ?"); params.append(as_of)
    # Tiebreaks — all no-ops on the demo path (period_end pinned, USD-only data), so
    # shipped behavior is unchanged; they only disambiguate the (fy,fp) benchmark path
    # and dual-currency/duplicate rows:
    #  - period_end DESC pins the period that CLOSES the fiscal period, beating the
    #    prior-year comparatives that share the same (fy, fp) on one filing.
    #  - (unit = 'USD') DESC prefers USD when a tag reports one period in several units
    #    (a per-share concept has no USD row, so this is a no-op for it). Default policy
    #    pending a registry unit model.
    #  - accession DESC is a final deterministic tiebreak so provenance is reproducible.
    usd_pref = "(unit = 'USD') DESC"
    if period_type == "duration":
        order = f"(period_end - period_start) DESC, {usd_pref}, period_end DESC, filed DESC, accession DESC"
    else:
        order = f"{usd_pref}, period_end DESC, filed DESC, accession DESC"
    sql = f"SELECT * FROM facts WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 1"
    res = con.execute(sql, params)
    row = res.fetchone()
    if row is None:
        return None
    return dict(zip([c[0] for c in res.description], row))


def _restated_later(con, r: dict, as_of) -> bool:
    """True if a filing after as_of changed this same (entity, tag, unit, period) fact.

    Compares value_exact (DECIMAL), not value (DOUBLE): float collapses distinct
    integers at/above 2^53, silently missing a restatement. Filters on unit so a
    same-period fact in another currency is never mistaken for a restatement.

    Deferred limitation: on the (fy, fp) benchmark path, a restatement that SHIFTS
    the period_end (52/53-week fiscal calendars) is not detected, because the probe
    keys on the selected row's period_end. Reconcile when the benchmark path gets its
    first consumer/test (P4) — see Docs/superpowers/.../*-deferred.md.
    """
    if as_of is None:
        return False
    q = ("SELECT 1 FROM facts WHERE cik = ? AND taxonomy = 'us-gaap' AND tag = ? "
         "AND unit IS NOT DISTINCT FROM ? "
         "AND period_end IS NOT DISTINCT FROM ? AND period_start IS NOT DISTINCT FROM ? "
         "AND filed > ? AND value_exact <> ? LIMIT 1")
    return con.execute(
        q, [r["cik"], r["tag"], r["unit"], r["period_end"], r["period_start"],
            r["filed"], r["value_exact"]]
    ).fetchone() is not None


def _build(q: Query, spec, r: dict, con) -> Evidence:
    prov = Provenance(r["fact_id"], r["cik"], r["accession"], r["filed"], r["form"],
                      r["taxonomy"], r["tag"], r["fy"], r["fp"], r["frame"])
    return Evidence(
        concept=q.concept,
        value=float(r["value"]) if r["value"] is not None else None,
        value_exact=r["value_exact"], unit=r["unit"], period=q.period, as_of=q.as_of,
        provenance=prov, found=True, tags_tried=spec.tags,
        restated_later=_restated_later(con, r, q.as_of),
    )


def _miss(q: Query, spec) -> Evidence:
    return Evidence(q.concept, None, None, None, q.period, q.as_of, None, False, spec.tags, None)


def retrieve_evidence(q: Query, con=None) -> Evidence:
    con = con or _conn()
    spec = registry.get_concept(q.concept)        # raises ConceptNotFound
    cik = _resolve_cik(con, q.entity)
    if cik is None:
        return _miss(q, spec)
    for tag in spec.tags:                          # first match wins
        r = _select(con, cik, tag, spec.period_type, q.period, q.as_of)
        if r is not None:
            return _build(q, spec, r, con)
    return _miss(q, spec)


def retrieve_evidence_batch(qs: Sequence[Query], con=None) -> list[Evidence]:
    con = con or _conn()
    return [retrieve_evidence(q, con=con) for q in qs]


def entity_has_tag(entity: str, tag: str, con=None) -> bool:
    con = con or _conn()
    cik = _resolve_cik(con, entity)
    if cik is None:
        return False
    return con.execute(
        "SELECT 1 FROM facts WHERE cik = ? AND tag = ? LIMIT 1", [cik, tag]).fetchone() is not None


def latest_filing(entity: str, form: str = "10-K", con=None) -> str | None:
    """Representative source for the Validate card: the entity's most recently
    filed accession of `form`."""
    con = con or _conn()
    cik = _resolve_cik(con, entity)
    if cik is None:
        return None
    row = con.execute(
        "SELECT accession FROM facts WHERE cik = ? AND form = ? ORDER BY filed DESC LIMIT 1",
        [cik, form]).fetchone()
    return row[0] if row else None
