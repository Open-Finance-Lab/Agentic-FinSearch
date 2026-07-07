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


def validation_gate(path, max_file_mb):
    """Input trust boundary (spec §7.1). Batch-level defects raise ValueError
    (poison pill, §6.1); a bad `published` drops only that story."""
    if path.stat().st_size > max_file_mb * 1024 * 1024:
        raise ValueError(f"file exceeds {max_file_mb}MB")
    mtime = path.stat().st_mtime
    lo, hi = mtime - 30 * 86400, mtime + 3600
    stories = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        story = json.loads(line)  # JSONDecodeError is a ValueError → poison pill
        for field in REQUIRED_FIELDS:
            if field not in story:
                raise ValueError(f"line {i}: missing required field {field}")
        if not (lo <= float(story["published"]) <= hi):
            continue  # forged/insane epoch: drop the story, keep the batch
        story["title"] = clean_text(story["title"], FIELD_CAPS["title"])
        story["description"] = clean_text(story.get("description", ""),
                                          FIELD_CAPS["description"])
        story["source"] = clean_text(story["source"], FIELD_CAPS["source"])
        story["link"] = str(story["link"])[:FIELD_CAPS["link"]]
        story["tickers"] = [t.upper() for t in story.get("tickers", [])]
        stories.append(story)
    return stories


# --- Subject-relevance gate (spec D8) -------------------------------------
# ROUNDUP_PATTERNS and TICKER_ALIASES are deliberately plain data: they are
# the candidate-quality tuning surface. >>> OWNER-TUNED: review before merge.
ROUNDUP_PATTERNS = (
    r"\bcompany news for\b",
    r"\bnews roundup\b",
    r"\bstocks to watch\b",
    r"\bearnings preview\b",
    r"\b\d+ (?:stocks|reasons|things)\b",
    r"\bjoined the dow\b",
)
ROUNDUP_RE = [re.compile(p) for p in ROUNDUP_PATTERNS]

# Lowercase name tokens per ticker (union watchlist, spec D2). Symbols shorter
# than SYMBOL_MATCH_MIN_LEN rely on aliases alone. >>> OWNER-TUNED.
TICKER_ALIASES = {
    "AAPL": ("apple",), "AMZN": ("amazon",),
    "AXP": ("american express", "amex"), "BA": ("boeing",),
    "BRK-B": ("berkshire",), "BTC-USD": ("bitcoin",),
    "CAT": ("caterpillar",), "CSCO": ("cisco",), "CVX": ("chevron",),
    "DIS": ("disney",), "GOOGL": ("google", "alphabet"),
    "GS": ("goldman",), "HD": ("home depot",), "IBM": ("ibm",),
    "INTC": ("intel",), "JNJ": ("johnson & johnson",),
    "JPM": ("jpmorgan", "jp morgan"), "KO": ("coca-cola",),
    "MA": ("mastercard",), "MCD": ("mcdonald",),
    "META": ("meta platforms", "facebook", "instagram"), "MMM": ("3m",),
    "MRK": ("merck",), "MSFT": ("microsoft",), "NKE": ("nike",),
    "NVDA": ("nvidia",), "PFE": ("pfizer",), "PG": ("procter",),
    "TRV": ("travelers",), "TSLA": ("tesla",), "UNH": ("unitedhealth",),
    "V": ("visa",), "WBA": ("walgreens",), "WMT": ("walmart",),
    "XOM": ("exxon",),
}
SYMBOL_MATCH_MIN_LEN = 3

# Aliases that collide with common dollar/share-count shorthand ("$3M",
# "133M shares") once lowercased — word-boundaries alone don't save them,
# since "$" and digit-to-nonword transitions are boundaries too. Excluded
# from matching only when immediately preceded by "$", "." or a digit.
_ALIAS_NUMERIC = frozenset({"3m"})


def _alias_pattern(alias):
    pattern = rf"\b{re.escape(alias)}\b"
    if alias in _ALIAS_NUMERIC:
        pattern = r"(?<![\d$.])" + pattern
    return re.compile(pattern)


# Precompiled per-alias patterns. Built once at import time from the
# owner-tuned TICKER_ALIASES data above.
ALIAS_RE = {ticker: [_alias_pattern(a) for a in aliases]
            for ticker, aliases in TICKER_ALIASES.items()}


def is_subject(title, ticker):
    """Entity-as-subject heuristic (spec D8): mention-only and roundup
    stories never reach the LLM. Alias matches are word-bounded, never a
    bare substring check — `"intel" in lowered` would also match inside
    "intelligence", `"cisco"` inside "francisco", `"visa"` inside
    "advisable". The numeric alias "3m" (MMM) additionally excludes a
    preceding "$", "." or digit so dollar/share-count figures ("$3M",
    "133M shares") don't false-tag MMM as subject."""
    lowered = title.lower()
    if any(p.search(lowered) for p in ROUNDUP_RE):
        return False
    if len(ticker) >= SYMBOL_MATCH_MIN_LEN and re.search(
            rf"(?<![A-Z0-9-]){re.escape(ticker)}(?![A-Z0-9-])", title):
        return True
    return any(p.search(lowered) for p in ALIAS_RE.get(ticker, ()))


# --- Candidate selection (spec §3 step 4, D9) --------------------------------
_TITLE_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def normalize_title(title):
    return " ".join(_TITLE_NORM_RE.sub(" ", title.lower()).split())


def collapse_near_dups(stories):
    """Keep the first story per normalized title (input sorted best-first).
    Duplicates must never inflate n_articles / satisfy the damper (spec D9)."""
    seen, kept, collapsed = set(), [], 0
    for story in stories:
        key = normalize_title(story["title"])
        if key in seen:
            collapsed += 1
            continue
        seen.add(key)
        kept.append(story)
    return kept, collapsed


def select_candidates(stories, watchlist, cfg):
    """Spec §3 step 4: editorial gate → subject gate (D8) → per-ticker
    best-first sort → near-dup collapse (D9) → cap."""
    diag = {"candidates_dropped_not_subject": 0, "near_dups_collapsed": 0,
            "tickers_capped": 0}
    by_ticker = {}
    for story in stories:
        if float(story["score"]) < cfg["min_editorial"]:
            continue
        for ticker in story["tickers"]:
            if ticker not in watchlist:
                continue
            if not is_subject(story["title"], ticker):
                diag["candidates_dropped_not_subject"] += 1
                continue
            by_ticker.setdefault(ticker, []).append(story)
    capped, n_articles = {}, {}
    for ticker in sorted(by_ticker):
        lst = by_ticker[ticker]
        lst.sort(key=lambda s: (-float(s["score"]), -float(s["published"])))
        lst, collapsed = collapse_near_dups(lst)
        diag["near_dups_collapsed"] += collapsed
        n_articles[ticker] = len(lst)
        if len(lst) > cfg["per_ticker_cap"]:
            diag["tickers_capped"] += 1
        capped[ticker] = lst[:cfg["per_ticker_cap"]]
    return capped, n_articles, diag
