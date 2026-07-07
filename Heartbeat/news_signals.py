#!/usr/bin/env python3
"""News → signals generator for the Agentic FinSearch heartbeat feed.

Sweeps $HEARTBEAT_HOME/digests/items-*.jsonl batches not yet in
signals_state.json through: validation gate → subject-relevance gate (D8) →
near-dup collapse (D9) → one batched, datamarked LLM call → guid-membership
join → atomic signals-*.json artifact (written BEFORE state, spec §6.2).

Design spec: Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md
Stdlib-only, single file (same deployability contract as news_heartbeat.py).
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2026-07-06.1"
SCHEMA_VERSION = 1
PROMPT_VERSION = 1

DEFAULT_WATCHLIST = "AAPL MSFT NVDA GOOGL AMZN META TSLA BRK-B JPM BTC-USD"
REQUIRED_FIELDS = ("guid", "title", "link", "source", "published", "score")
FIELD_CAPS = {"title": 500, "description": 5000, "link": 2000, "source": 200}
LLM_TIMEOUT = 120
LLM_RETRIES = 1

# Control chars + bidi/direction overrides (recency/spoofing hygiene, spec §7.1).
CONTROL_RE = re.compile(
    "[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f"
    "\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]"
)

# Datamarking delimiters (spec §4.3): candidate text is wrapped in these and
# declared untrusted. clean_text() strips the token from all input so feed
# text can never forge a boundary.
MARK_OPEN = "<<<NEWS_DATA"
MARK_CLOSE = "NEWS_DATA>>>"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_env_file(path):
    """KEY=VALUE env file loader — same semantics as news_heartbeat.py."""
    path = Path(path)
    if not path.exists():
        sys.exit(f"env file not found: {path} "
                 f"(copy Heartbeat/.env.heartbeat.example and fill it in)")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"')
        if " #" in value:  # inline comments are not part of the value
            value = value.split(" #", 1)[0].rstrip()
        if key in os.environ:
            log(f"env file: {key} already set in environment — keeping "
                f"process value")
        else:
            os.environ[key] = value


def clean_text(s, cap):
    s = CONTROL_RE.sub("", unicodedata.normalize("NFC", s or ""))
    s = s.replace("NEWS_DATA", "")  # marker token can never come from the feed
    return s[:cap]


def load_config():
    home = Path(os.environ.get("SIGNALS_HOME")
                or os.environ.get("HEARTBEAT_HOME",
                                  Path.home() / "fingpt" / "heartbeat"))
    return {
        "home": home,
        "digests": home / "digests",
        "signals_dir": home / "signals",
        "state_path": home / "signals_state.json",
        "model": os.environ.get("SIGNALS_MODEL")
                 or os.environ.get("HEARTBEAT_MODEL", "gpt-4o-mini"),
        "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
        "watchlist": sorted(set(
            os.environ.get("HEARTBEAT_WATCHLIST", DEFAULT_WATCHLIST).split())),
        "window_hours": int(os.environ.get("HEARTBEAT_WINDOW_HOURS", "24")),
        "min_editorial": float(os.environ.get("SIGNALS_MIN_EDITORIAL_SCORE", "2.0")),
        "per_ticker_cap": int(os.environ.get("SIGNALS_PER_TICKER_CAP", "3")),
        "desc_cap": int(os.environ.get("SIGNALS_DESC_CAP", "200")),
        "threshold": float(os.environ.get("SIGNALS_THRESHOLD", "0.20")),
        "damp_cap": float(os.environ.get("SIGNALS_DAMP_CAP", "0.7")),
        "damp_min_articles": int(os.environ.get("SIGNALS_DAMP_MIN_ARTICLES", "2")),
        "max_file_mb": int(os.environ.get("SIGNALS_MAX_FILE_MB", "10")),
        "staleness_alert_h": float(os.environ.get("SIGNALS_STALENESS_ALERT_H", "20")),
    }
