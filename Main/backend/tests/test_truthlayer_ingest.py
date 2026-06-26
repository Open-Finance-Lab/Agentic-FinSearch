from truthlayer import store, ingest

# Minimal companyfacts shape: one instant tag with an ORIGINAL and a RESTATED entry
# for the same period (different accn/filed/val), proving restatements coexist.
SYNTHETIC = {
    "cik": 999999,
    "entityName": "Test Co",
    "facts": {
        "us-gaap": {
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        {"end": "2023-12-31", "val": 1000, "accn": "acc-1", "fy": 2023,
                         "fp": "FY", "form": "10-K", "filed": "2024-02-01", "frame": "CY2023Q4I"},
                        {"end": "2023-12-31", "val": 1100, "accn": "acc-2", "fy": 2023,
                         "fp": "FY", "form": "10-K", "filed": "2025-02-01"},
                    ]
                },
            },
            "Revenues": {
                "units": {
                    "USD": [
                        {"start": "2023-01-01", "end": "2023-12-31", "val": 500, "accn": "acc-1",
                         "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
                    ]
                }
            },
        }
    },
}


def test_companyfacts_rows_maps_instant_and_duration():
    rows = list(ingest.companyfacts_rows(SYNTHETIC))
    by_tag = {}
    for r in rows:
        by_tag.setdefault(r[3], []).append(r)   # r[3] == tag (FACT_COLUMNS order)
    assert len(by_tag["Assets"]) == 2           # original + restatement both present
    # instant fact has period_start None; duration has a start
    assets = by_tag["Assets"][0]
    rev = by_tag["Revenues"][0]
    ps_idx = store.FACT_COLUMNS.index("period_start")
    assert assets[ps_idx] is None
    assert rev[ps_idx] is not None


def test_ingest_is_idempotent():
    con = store.connect(":memory:")
    ingest.ingest_doc(con, SYNTHETIC)
    ingest.ingest_doc(con, SYNTHETIC)            # re-ingest must not duplicate
    n = con.execute("SELECT count(*) FROM facts").fetchone()[0]
    assert n == 3                                # 2 Assets + 1 Revenues
    assert con.execute("SELECT count(*) FROM entities").fetchone()[0] == 1
