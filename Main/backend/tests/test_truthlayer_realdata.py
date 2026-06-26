from datetime import date

import pytest

from truthlayer import store, ingest, retrieve
from truthlayer.contracts import Query, Period


@pytest.fixture(scope="module")
def con():
    c = store.connect(":memory:")
    import json
    for path in sorted(ingest.CF_DIR.glob("CIK*.json")):
        doc = json.loads(path.read_text())
        ingest.ingest_doc(c, doc)
    ticker_by_cik = {cik: tk for tk, cik in ingest.DEMO_CIKS.items()}
    for cik, tk in ticker_by_cik.items():
        c.execute("UPDATE entities SET ticker = ? WHERE cik = ?", [tk, cik])
    return c


def _val(con, ticker, concept, period_end):
    ev = retrieve.retrieve_evidence(
        Query(ticker, concept, Period(period_end=date.fromisoformat(period_end))), con=con)
    return ev.value


def test_aapl_balance_sheet(con):
    assert _val(con, "AAPL", "assets", "2023-09-30") == pytest.approx(352583e6, rel=1e-6)
    assert _val(con, "AAPL", "liabilities", "2023-09-30") == pytest.approx(290437e6, rel=1e-6)
    assert _val(con, "AAPL", "equity", "2023-09-30") == pytest.approx(62146e6, rel=1e-6)


def test_aapl_gross_margin_inputs(con):
    assert _val(con, "AAPL", "revenue", "2023-09-30") == pytest.approx(383285e6, rel=1e-6)
    assert _val(con, "AAPL", "cost_of_revenue", "2023-09-30") == pytest.approx(214137e6, rel=1e-6)


def test_tsla_temporary_equity_resolves(con):
    assert _val(con, "TSLA", "temporary_equity", "2023-12-31") == pytest.approx(242e6, rel=1e-6)
