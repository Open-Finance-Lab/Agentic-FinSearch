#!/usr/bin/env python3
"""Regenerate signals-fixture.json deterministically (no network, fixed clock).

Run from Heartbeat/:  python3 tests/fixtures/make_signals_fixture.py
Commit the result. test_news_signals.TestFixture guards against drift.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import news_signals as ns

NOW = 1783350000.0  # 2026-07-06T12:20:00Z — fixed so the artifact is stable

ITEMS = [
    {"guid": "fix-msft-1", "title": "Microsoft raises Azure guidance after record quarter",
     "link": "https://example.com/msft-1", "source": "Reuters",
     "published": NOW - 4 * 3600, "description": "Cloud revenue outlook lifted.",
     "tickers": ["MSFT"], "feeds": ["news"], "score": 6.0},
    {"guid": "fix-msft-2", "title": "Microsoft cloud momentum lifts outlook, analysts say",
     "link": "https://example.com/msft-2", "source": "Barchart",
     "published": NOW - 6 * 3600, "description": "Analysts raise targets.",
     "tickers": ["MSFT"], "feeds": ["news"], "score": 4.5},
    {"guid": "fix-msft-3", "title": "Microsoft raises Azure guidance after record quarter!",
     "link": "https://example.com/msft-3", "source": "Yahoo",
     "published": NOW - 5 * 3600, "description": "Syndicated copy.",
     "tickers": ["MSFT"], "feeds": ["aapl"], "score": 3.0},
    {"guid": "fix-nvda-1", "title": "Nvidia reports record datacenter orders",
     "link": "https://example.com/nvda-1", "source": "CNBC",
     "published": NOW - 3 * 3600, "description": "Orders hit a record.",
     "tickers": ["NVDA"], "feeds": ["news"], "score": 7.0},
    {"guid": "fix-roundup", "title": "Company News for July 6, 2026",
     "link": "https://example.com/roundup", "source": "Zacks",
     "published": NOW - 2 * 3600, "description": "AAPL, GOOGL and others moved.",
     "tickers": ["AAPL", "GOOGL"], "feeds": ["aapl"], "score": 5.0},
    {"guid": "fix-googl-mention", "title": "This AI stock joined the Dow — what it means",
     "link": "https://example.com/mention", "source": "Motley Fool",
     "published": NOW - 8 * 3600, "description": "Listicle.",
     "tickers": ["GOOGL"], "feeds": ["news"], "score": 3.0},
]


def fake_llm(cfg, system, user):
    return {
        "overview": "Fixture batch: cloud strength at Microsoft and Nvidia; "
                    "the rest of the tape is quiet.",
        "tickers": {
            "MSFT": {"score": 0.5, "guid": "fix-msft-1",
                     "rationale": "Two distinct outlets report upbeat Azure guidance."},
            "NVDA": {"score": 0.9, "guid": "fix-nvda-1",
                     "rationale": "Single story reports record datacenter orders."},
        },
    }


def build():
    with tempfile.TemporaryDirectory() as td:
        items = Path(td) / "items-fixture.jsonl"
        items.write_text(
            "\n".join(json.dumps(s) for s in ITEMS) + "\n", encoding="utf-8")
        os.utime(items, (NOW, NOW))  # published-sanity gate anchors on mtime
        with_env = {"HEARTBEAT_WATCHLIST": "AAPL GOOGL MSFT NVDA"}
        saved = {k: os.environ.get(k) for k in with_env}
        os.environ.update(with_env)
        try:
            cfg = ns.load_config()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return ns.process_batch(items, cfg, NOW, llm=fake_llm)


if __name__ == "__main__":
    out = Path(__file__).parent / "signals-fixture.json"
    out.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {out}")
