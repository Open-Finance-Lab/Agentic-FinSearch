#!/usr/bin/env python3
"""Agentic FinSearch — News Heartbeat.

Fetches Yahoo Finance news (market-wide RSS + per-ticker RSS), aggregates,
de-duplicates, ranks, summarizes (one LLM call, extractive fallback), writes a
digest log, and optionally posts it to a Discord channel via the Bot REST API.

Stdlib only, by design: runs on a 1 vCPU / 2 GB droplet with no pip and no venv.
Design doc: Docs/superpowers/specs/2026-06-10-news-heartbeat-design.md
"""

import argparse
import email.utils
import html
import json
import math
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2026-07-14.1"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
MARKET_FEEDS = {
    "topstories": "https://finance.yahoo.com/rss/topstories",
    "rssindex": "https://finance.yahoo.com/news/rssindex",
}
TICKER_FEED = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
               "?s={ticker}&region=US&lang=en-US")
# Dow Jones Industrial Average constituents.
# Source: S&P Dow Jones Indices; effective 2026-06-29 (GOOGL replaced VZ).
# Reconcile against the official index — never a hand-maintained copy — when
# the composition changes. Parity-tested against news_heartbeat.py
# (Heartbeat/tests/test_watchlist.py). To add/remove a ticker, edit this
# list and its TICKER_ALIASES entry (news_signals.py only; test-enforced).
DOW_30 = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
]
# Non-Dow tickers FinSearch also tracks for its own community digests.
WATCHLIST_EXTRAS = ["META", "TSLA", "BRK-B", "BTC-USD"]
DEFAULT_WATCHLIST = " ".join(sorted(set(DOW_30) | set(WATCHLIST_EXTRAS)))
DISCLAIMER = ("Summaries by Agentic FinSearch · Sources: Yahoo Finance & linked "
              "publishers · Not financial advice")

# Discord hard limits: 4096 chars per embed description, 6000 per message.
# Packing to 3900 leaves headroom for embed title + footer under both.
EMBED_PACK_LIMIT = 3900
HEADLINE_LIMIT = 200     # feed headline cap inside rendered masked links
SUMMARY_CAP = 500        # per-item summary cap enforced on LLM output
# Minimum share of a summary's content words that must appear in its story's
# title+description. Calibrated on the 2026-06-10 dry runs: faithful LLM
# summaries scored 0.64-0.88, a cross-wired one (another story's summary on
# this story's guid) scored 0.07.
SUMMARY_OVERLAP_MIN = 0.34
LLM_DESC_CAP = 300       # description chars sent to the LLM per candidate
CANDIDATE_CAP = 25       # ranked stories offered to the LLM / fallback digest
OG_HEAD_BYTES = 262144   # how much of an article page to read for og: tags

TIER1 = ("reuters", "bloomberg", "associated press", "wall street journal",
         "financial times", "cnbc", "barron")
TIER2 = ("investor's business daily", "marketwatch", "fortune",
         "business insider", "thestreet", "yahoo finance")
TIER_LOW = ("motley fool", "stockstory", "zacks", "simply wall st",
            "24/7 wall st", "benzinga", "insider monkey")
BOOST_KEYWORDS = (
    "earnings", "fed", "rate cut", "rate hike", "rates", "inflation",
    "acquisition", "acquire", "merger", "guidance", "bankrupt", "upgrade",
    "downgrade", "ipo", "antitrust", "tariff", "layoff", "forecast", "outlook",
    "treasury", "gdp", "jobs report", "dividend", "buyback", "stock split",
    "lawsuit", "sec charges", "default", "downturn", "rally", "selloff")
PENALTY_PATTERNS = (
    "credit card", "insurance cover", "renters insurance", "mortgage rate",
    "savings account", "how to", "should you", "things to know", "side hustle",
    "social security check", "calculator", "quiz", "best ways", " tips")
PENALTY_RE = re.compile(r"^\d+\s+(things|ways|stocks|reasons|signs)\b", re.I)

# Per-ticker feeds carry no <source> tag; the link-domain fallback must map to
# publisher names or the source-tier scoring and attribution both degrade.
SOURCE_NAMES = {
    "finance.yahoo.com": "Yahoo Finance",
    "fool.com": "Motley Fool",
    "247wallst.com": "24/7 Wall St",
    "insidermonkey.com": "Insider Monkey",
    "thestreet.com": "TheStreet",
    "wsj.com": "Wall Street Journal",
    "barrons.com": "Barron's",
    "investors.com": "Investor's Business Daily",
    "simplywall.st": "Simply Wall St",
    "benzinga.com": "Benzinga",
    "zacks.com": "Zacks",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "cnbc.com": "CNBC",
    "marketwatch.com": "MarketWatch",
    "businessinsider.com": "Business Insider",
    "fortune.com": "Fortune",
    "investopedia.com": "Investopedia",
    "observer.com": "Observer",
    "gurufocus.com": "GuruFocus",
    "stocktwits.com": "Stocktwits",
    "seekingalpha.com": "Seeking Alpha",
    "investorplace.com": "InvestorPlace",
    "barchart.com": "Barchart",
    "globenewswire.com": "GlobeNewswire",
    "businesswire.com": "Business Wire",
    "prnewswire.com": "PR Newswire",
    "ft.com": "Financial Times",
}

# Live-blog/roundup titles whose RSS description drifts to a different story
ROUNDUP_TITLE_RE = re.compile(
    r"(?i)^(stock market today|markets? (live|wrap)|live updates)")

STOPWORDS = frozenset(
    "a an and are as at be but by for from has in is it its of on that the to "
    "was will with this these those after amid over under new says said".split())


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def write_jsonl_atomic(path, rows):
    """Write rows as JSONL via temp + os.replace so a reader (the signals
    sweep) can never observe a half-written batch (signals spec §3)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- parsing ---

def _parse_date(text):
    if not text:
        return 0.0
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if dt.tzinfo is None:  # zoneless feed timestamps are UTC, not droplet-local
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def _host(url):
    return (urllib.parse.urlsplit(url or "").hostname or "").lower()


def _yahoo_host(host):
    return host == "yahoo.com" or host.endswith(".yahoo.com")


def _source_from_link(link):
    host = _host(link).removeprefix("www.")
    if host == "finance.yahoo.com" and "/m/" in link:
        return "via Yahoo Finance"  # Yahoo-hosted syndication, publisher unknown
    return SOURCE_NAMES.get(host, host or "unknown")


DTD_RE = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)


def parse_rss(xml_text, feed, ticker=None):
    """Parse one Yahoo RSS document (either schema) into Story dicts."""
    if DTD_RE.search(xml_text or ""):
        return []  # XXE / billion-laughs guard: legit feeds never carry a DTD
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    stories = []
    for item in root.iter("item"):
        title = _strip_html(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        if ".tsrc=rss" in link and not _yahoo_host(_host(link)):
            link = link.split("?.tsrc=rss")[0]  # inert Yahoo param off-domain
        guid = (item.findtext("guid") or "").strip() or link.rstrip("/").rsplit("/", 1)[-1]
        source = (item.findtext("source") or "").strip()
        description = _strip_html(item.findtext("description"))
        if description and ROUNDUP_TITLE_RE.search(title):
            description = ""  # live-blog descriptions drift to other stories
        stories.append({
            "guid": guid,
            "title": title,
            "link": link,
            "source": source or _source_from_link(link),
            "published": _parse_date(item.findtext("pubDate")),
            "description": description,
            "tickers": [ticker] if ticker else [],
            "feeds": [feed],
        })
    return stories


# ----------------------------------------------------- merge / dedupe ------

def _union(a, b):
    return list(dict.fromkeys([*a, *b]))


def merge_stories(story_lists):
    """Union all feeds' stories, deduplicating by guid."""
    by_guid = {}
    for stories in story_lists:
        for s in stories:
            prev = by_guid.get(s["guid"])
            if prev is None:
                by_guid[s["guid"]] = dict(s)
                continue
            prev["feeds"] = _union(prev["feeds"], s["feeds"])
            prev["tickers"] = _union(prev["tickers"], s["tickers"])
            if len(s["description"]) > len(prev["description"]):
                prev["description"] = s["description"]
            prev["published"] = prev["published"] or s["published"]
    return list(by_guid.values())


def title_tokens(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in STOPWORDS}


def collapse_near_dups(stories, threshold=0.7):
    """Collapse near-identical headlines; the higher-scored story survives."""
    kept = []  # (story, tokens) pairs — tokenize each title exactly once
    for s in sorted(stories, key=lambda x: x.get("editorial_score", 0.0), reverse=True):
        toks = title_tokens(s["title"])
        for k, ktoks in kept:
            union = toks | ktoks
            if union and len(toks & ktoks) / len(union) >= threshold:
                k["tickers"] = _union(k["tickers"], s["tickers"])
                k["feeds"] = _union(k["feeds"], s["feeds"])
                break
        else:
            kept.append((s, toks))
    return [k for k, _ in kept]


# ------------------------------------------------------- window / state ----

def window_floor(now, hours):
    """Oldest publish time still inside the digest window — the single
    definition shared by windowing and the seen-state computation."""
    return now - hours * 3600


def filter_window(stories, now, hours, seen):
    lo = window_floor(now, hours)
    return [s for s in stories
            if lo <= s["published"] <= now + 3600 and s["guid"] not in seen]


def prune_state(state, now, max_age_days=7):
    lo = now - max_age_days * 86400
    return {guid: ts for guid, ts in state.items() if ts >= lo}


# ------------------------------------------------------------- ranking -----

def score_story(story, watchlist, now):
    score = 0.0
    source = story["source"].lower()
    if any(t in source for t in TIER1):
        score += 3.0
    elif any(t in source for t in TIER2):
        score += 2.0
    elif any(t in source for t in TIER_LOW):
        score += 0.5
    else:
        score += 1.0
    if len(story["feeds"]) >= 2:
        score += 2.0
    if len(story["tickers"]) >= 2:
        score += 1.0
    if any(t in watchlist for t in story["tickers"]):
        score += 1.5
    title = story["title"].lower()
    hits = sum(1 for kw in BOOST_KEYWORDS if kw in title)
    score += min(hits, 3)
    text = title + " " + story["description"][:200].lower()
    if any(p in text for p in PENALTY_PATTERNS) or PENALTY_RE.search(story["title"]):
        score -= 3.0
    if story["published"] >= now - 6 * 3600:
        score += 1.0
    return score


def rank_stories(stories, watchlist, now):
    for s in stories:
        s["editorial_score"] = score_story(s, watchlist, now)
    return sorted(stories, key=lambda s: (s["editorial_score"], s["published"]),
                  reverse=True)


# ------------------------------------------------------------- digesting ---

def _first_sentence(text):
    """First sentence; a boundary needs a following capital, so abbreviations
    mid-sentence (U.S. stocks, Inc. shares, vs. rivals) don't split."""
    text = " ".join((text or "").split())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"'(])", text, maxsplit=1)
    return parts[0] if parts and parts[0] else text


def _quoted_excerpt(description):
    """Publisher prose is excerpted, not paraphrased — mark it as quotation.
    Capped before quoting so truncation can never eat the closing mark."""
    excerpt = textwrap.shorten(_first_sentence(description),
                               width=SUMMARY_CAP - 2, placeholder="…")
    return f"“{excerpt}”"


def extractive_digest(stories, now, max_per_section=8):
    """Deterministic fallback digest when no LLM is available."""
    ranked = sorted(stories, key=lambda s: s.get("editorial_score", 0.0), reverse=True)
    market = [s for s in ranked if not s["tickers"]][:max_per_section]
    company = [s for s in ranked if s["tickers"]][:max_per_section]
    sections = []
    for sec_title, group in (("📈 Market Pulse", market), ("🏢 Company Watch", company)):
        items = [{"guid": s["guid"],
                  "summary": (_quoted_excerpt(s["description"])
                              if s["description"] else s["title"])}
                 for s in group]
        if items:
            sections.append({"title": sec_title, "items": items})
    n = sum(len(sec["items"]) for sec in sections)
    overview = (f"{n} notable stories from Yahoo Finance in the last 24 hours "
                f"(extractive digest — automated selection without LLM curation).")
    return {"overview": overview, "sections": sections}


def summary_grounded(summary, story, threshold=SUMMARY_OVERLAP_MIN):
    """True when the summary shares enough vocabulary with the story's
    title+description to plausibly describe it. The LLM sees only that text,
    so a faithful summary must reuse much of it; near-zero overlap means the
    LLM paired this guid with a different story's summary."""
    toks = title_tokens(summary)
    src = title_tokens(story["title"]) | title_tokens(story["description"])
    return bool(toks) and len(toks & src) / len(toks) >= threshold


def validate_llm_digest(payload, candidates):
    """Sanitize the LLM's digest JSON; None if unusable."""
    by_guid = {s["guid"]: s for s in candidates}
    if not isinstance(payload, dict):
        return None
    overview = payload.get("overview")
    sections_in = payload.get("sections")
    if not isinstance(overview, str) or not overview.strip():
        return None
    if not isinstance(sections_in, list):
        return None
    sections = []
    used = set()
    for sec in sections_in:
        if not isinstance(sec, dict) or not isinstance(sec.get("items"), list):
            continue
        items = []
        for item in sec["items"]:
            if not (isinstance(item, dict) and item.get("guid") in by_guid
                    and item["guid"] not in used
                    and isinstance(item.get("summary"), str)
                    and item["summary"].strip()):
                continue
            story = by_guid[item["guid"]]
            summary = item["summary"]
            if not summary_grounded(summary, story):
                if not story["description"]:
                    log(f"WARN ungrounded summary for {item['guid']} and no "
                        f"description to excerpt — item dropped")
                    continue
                log(f"WARN ungrounded summary for {item['guid']} — replaced "
                    f"with publisher excerpt")
                summary = _quoted_excerpt(story["description"])
            used.add(item["guid"])
            items.append({"guid": item["guid"],
                          "summary": textwrap.shorten(summary, width=SUMMARY_CAP,
                                                      placeholder="…")})
        if items:
            sections.append({"title": str(sec.get("title") or "News"),
                             "items": items})
    if not sections:
        return None
    return {"overview": overview.strip(), "sections": sections}


# ------------------------------------------------------------- rendering ---

def _md_escape(text):
    """Neutralize masked-link injection from attacker-controlled feed text.

    Square brackets become parentheses: without ']' no [text](url) form can be
    smuggled, and unlike backslash escapes the result renders identically on
    every Discord client."""
    return (text or "").replace("[", "(").replace("]", ")")


def _masked_link(title, link):
    """The single site that builds [text](url) markdown from feed data."""
    return f"**[{_md_escape(title)}]({link})**"


def _age_label(published, now):
    hours = max(0, int((now - published) / 3600))
    return f"{hours}h ago" if hours < 48 else f"{hours // 24}d ago"


def _utc_date(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _item_line(item, idx, now):
    s = idx[item["guid"]]
    return (f"- {_masked_link(s['title'], s['link'])} — "
            f"{_md_escape(item['summary'])} "
            f"*({_md_escape(s['source'])}, {_age_label(s['published'], now)})*")


def render_markdown(digest, idx, now):
    lines = ["# Agentic FinSearch — Daily Market Digest",
             f"**{_utc_date(now)} UTC**", "",
             f"> {_md_escape(digest['overview'])}", ""]
    for sec in digest["sections"]:
        lines.append(f"## {_md_escape(sec['title'])}")
        for item in sec["items"]:
            if item["guid"] in idx:
                lines.append(_item_line(item, idx, now))
        lines.append("")
    lines += ["---", DISCLAIMER, ""]
    return "\n".join(lines)


def _embed_block(item, idx):
    s = idx[item["guid"]]
    return (f"{_masked_link(s['title'][:HEADLINE_LIMIT], s['link'])}\n"
            f"{_md_escape(item['summary'])} — *{_md_escape(s['source'])}*")


def discord_messages(digest, idx):
    """Render the digest as Discord messages (one embed each, limits enforced)."""
    blocks = [f"> {_md_escape(digest['overview'])}"]
    for sec in digest["sections"]:
        blocks.append(f"__**{_md_escape(sec['title'])}**__")
        for item in sec["items"]:
            if item["guid"] in idx:
                blocks.append(_embed_block(item, idx))
    packed, current = [], ""
    for block in blocks:
        block = block[:EMBED_PACK_LIMIT]
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > EMBED_PACK_LIMIT:
            packed.append(current)
            current = block
        else:
            current = candidate
    if current:
        packed.append(current)
    messages = []
    for i, desc in enumerate(packed):
        embed = {
            "title": ("📰 Agentic FinSearch — Daily Market Digest" if i == 0
                      else "📰 Daily Market Digest (continued)"),
            "description": desc,  # packing already caps under Discord limits
            "color": 0x2E86C1,
            # the compliance line must survive partial-post failures
            "footer": {"text": DISCLAIMER},
        }
        messages.append({"embeds": [embed],
                         "allowed_mentions": {"parse": []}})
    return messages


# ------------------------------------------------------------- fetching ----

def fetch_url(url, timeout=20, max_bytes=2 * 1024 * 1024, opener=None):
    req = urllib.request.Request(url, headers=HEADERS)
    open_fn = opener.open if opener else urllib.request.urlopen
    with open_fn(req, timeout=timeout) as resp:
        return resp.read(max_bytes).decode("utf-8", errors="replace")


def _fetch_feed(url, feed, ticker=None):
    """Fetch and parse one feed; None (with a WARN) on network failure."""
    try:
        return parse_rss(fetch_url(url), feed=feed, ticker=ticker)
    except (urllib.error.URLError, OSError) as exc:
        log(f"WARN feed {feed} failed: {exc}")
        return None


def looks_like_market_clone(ticker_stories, market_guids, threshold=0.9):
    """Detect Yahoo's fake-alive trap: unknown /rss/* paths return topstories."""
    if not ticker_stories:
        return False
    overlap = sum(1 for s in ticker_stories if s["guid"] in market_guids)
    return overlap / len(ticker_stories) >= threshold


def fetch_all(watchlist, pause=1.0):
    """Fetch market + per-ticker feeds politely; returns list of story lists."""
    story_lists = []
    market_guids = set()
    for feed, url in MARKET_FEEDS.items():
        stories = _fetch_feed(url, feed)
        if stories is not None:
            log(f"fetched {feed}: {len(stories)} items")
            story_lists.append(stories)
            market_guids.update(s["guid"] for s in stories)
        time.sleep(pause)
    for ticker in watchlist:
        feed = f"ticker:{ticker}"
        stories = _fetch_feed(TICKER_FEED.format(ticker=ticker), feed, ticker)
        if stories is not None:
            if looks_like_market_clone(stories, market_guids):
                log(f"WARN feed {feed} looks like a market-feed clone "
                    f"(fake-alive trap) — skipped")
            else:
                log(f"fetched {feed}: {len(stories)} items")
                story_lists.append(stories)
        time.sleep(pause)
    return story_lists


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 3xx raises HTTPError instead of being followed


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _og_host_ok(url):
    """Enrichment fetches only ever hit Yahoo over http(s) — SSRF guard."""
    return (urllib.parse.urlsplit(url).scheme in ("http", "https")
            and _yahoo_host(_host(url)))


OG_DESC_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)',
    re.I)
OG_DESC_RE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:description',
    re.I)


def fetch_og_description(link):
    """og:description from a Yahoo article page.

    Returns None when the host is off the allowlist (no request made — the
    SSRF policy lives here and only here), "" when a fetch was attempted but
    yielded nothing."""
    if not _og_host_ok(link):
        return None
    try:
        head = fetch_url(link, timeout=15, max_bytes=OG_HEAD_BYTES,
                         opener=_NO_REDIRECT_OPENER)
    except (urllib.error.URLError, OSError):
        return ""
    m = OG_DESC_RE.search(head) or OG_DESC_RE_REV.search(head)
    return html.unescape(m.group(1)).strip() if m else ""


def enrich_descriptions(stories, cap=8, pause=1.0):
    """Fill missing descriptions for the top-ranked stories via og:description."""
    fetched = 0
    for s in stories:
        if fetched >= cap:
            break
        # roundup pages drift like their RSS descriptions — never enrich them
        if s["description"] or ROUNDUP_TITLE_RE.search(s["title"]):
            continue
        desc = fetch_og_description(s["link"])
        if desc is None:
            continue  # host not eligible — no request was spent
        s["description"] = desc
        fetched += 1
        time.sleep(pause)
    if fetched:
        log(f"enriched {fetched} stories via og:description")


# ------------------------------------------------------------- LLM ---------

LLM_SYSTEM = (
    "You are the news editor for Agentic FinSearch, a financial research agent "
    "built at Columbia University's SecureFinAI Lab. You curate a daily market "
    "digest for a community of value investors, analysts, and finance students. "
    "Editorial rules: factual and sober; no hype, no speculation, no investment "
    "advice; ground every summary ONLY in the provided title and description — "
    "never invent facts, numbers, or causes; prefer market-moving news "
    "(macro, earnings, M&A, regulation) over promotional or personal-finance "
    "content.")

LLM_INSTRUCTIONS = (
    "From the candidate stories above, build today's digest as JSON:\n"
    '{"overview": "<2-3 sentence market overview synthesized from the '
    'candidates>", "sections": [{"title": "📈 Market Pulse", "items": '
    '[{"guid": "<id>", "summary": "<1-2 sentences>"}]}, {"title": '
    '"🏢 Company Watch", "items": [...]}]}\n'
    "Section definitions: Market Pulse = indexes, macro, rates, policy, "
    "sector/ETF moves; Company Watch = single-company news.\n"
    "Selection rules: pick the 5-8 most consequential stories for Market "
    "Pulse and up to 6 for Company Watch; use each story at most once; cover "
    "each distinct EVENT at most once across the whole digest — when several "
    "candidates describe the same move, keep only the most informative one; "
    "at most one Company Watch item per company unless the stories are "
    "clearly unrelated; prefer thematic diversity over repetition; skip "
    "low-value listicles and promotional content; use ONLY guid values from "
    "the candidates.\n"
    "Summary rules: each summary MUST describe the story stated in the "
    "item's TITLE — if the description discusses a different company or "
    "event than the title (common for 'Stock Market Today' style live-blog "
    "roundups), ignore the description and summarize from the title alone; "
    "never let description text override the headline; summaries must not "
    "merely restate the headline — add the most concrete detail (figure, "
    "name, magnitude) the description provides; ground everything in the "
    "given title/description only; attach each summary to the guid of the "
    "story it summarizes — never pair it with a different story's guid.\n"
    "Overview rules: cover the 2-3 most consequential distinct themes, "
    "stating only what the sources report; no filler or forward-looking "
    "phrases such as 'moving forward', 'remains to be seen', or 'investors "
    "are closely watching'; no predictions unless attributed to a source.\n"
    "Output strict JSON, nothing else.")


def _extract_llm_content(data):
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def llm_digest(candidates, api_key, model, base_url, now):
    """One chat.completions call. Returns a validated digest dict or None."""
    payload_stories = [{
        "guid": s["guid"],
        "title": s["title"],
        "source": s["source"],
        "age_hours": round((now - s["published"]) / 3600, 1),
        "tickers": s["tickers"],
        "description": textwrap.shorten(s["description"], width=LLM_DESC_CAP,
                                        placeholder="…"),
    } for s in candidates]
    body = json.dumps({
        "model": model,
        "temperature": 0.3,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {"role": "user", "content":
                "Candidate stories (JSON):\n" + json.dumps(payload_stories)
                + "\n\n" + LLM_INSTRUCTIONS},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = _extract_llm_content(data)
        if content is None:
            log("WARN LLM returned non-string content — falling back")
            return None
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        digest = validate_llm_digest(json.loads(content), candidates)
        if digest is None:
            log("WARN LLM returned unusable digest JSON — falling back")
        return digest
    except (urllib.error.URLError, OSError, KeyError, IndexError,
            AttributeError, TypeError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError; the net is wide on purpose:
        # an LLM hiccup must degrade to the extractive digest, never crash
        log(f"WARN LLM call failed ({type(exc).__name__}: {exc}) — falling back")
        return None


# ------------------------------------------------------------- discord -----

def post_discord(messages, token, channel_id):
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    for i, message in enumerate(messages):
        req = urllib.request.Request(
            url, data=json.dumps(message).encode("utf-8"), headers={
                "Content-Type": "application/json",
                "Authorization": f"Bot {token}",
                "User-Agent": "AgenticFinSearch-Heartbeat (agenticfinsearch.org, v1)",
            })
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                log(f"posted Discord message {i + 1}/{len(messages)}")
                break
            except (urllib.error.URLError, OSError) as exc:
                wait = 2 ** (attempt + 1)
                if isinstance(exc, urllib.error.HTTPError):
                    retry_after = exc.headers.get("Retry-After")
                    if exc.code == 429 and retry_after:
                        # header is server input: non-numeric, NaN, negative,
                        # or absurd values keep the exponential backoff
                        try:
                            parsed = float(retry_after) + 1
                            if math.isfinite(parsed) and parsed > 0:
                                wait = min(parsed, 60)
                        except ValueError:
                            pass
                    detail = exc.read(500).decode("utf-8", errors="replace")
                    log(f"WARN Discord HTTP {exc.code} (attempt {attempt + 1}/3): "
                        f"{detail[:200]}")
                else:
                    log(f"WARN Discord post failed (attempt {attempt + 1}/3): {exc}")
                if attempt == 2:
                    raise
                time.sleep(wait)
        time.sleep(1)


# ------------------------------------------------------------- main --------

def compute_seen_updates(merged, fresh, now, hours):
    """Guids safe to mark seen: digested ones, plus valid-dated stories already
    older than the window. Future/undated stories must stay unseen so they get
    a chance at the next beat."""
    seen = {s["guid"] for s in fresh}
    lo = window_floor(now, hours)
    seen.update(s["guid"] for s in merged if 0 < s["published"] < lo)
    return seen


def load_env_file(path):
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


def prune_old_digests(digests, now, max_age_days=90):
    for pattern in ("*.md", "*.jsonl"):
        for path in digests.glob(pattern):
            try:
                if now - path.stat().st_mtime > max_age_days * 86400:
                    path.unlink()
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agentic FinSearch news heartbeat")
    parser.add_argument("--dry-run", action="store_true",
                        help="never post to Discord, regardless of env")
    parser.add_argument("--env-file", help="load KEY=VALUE pairs before running")
    args = parser.parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)

    dry_run = args.dry_run or os.environ.get("HEARTBEAT_DRY_RUN", "1") != "0"
    watchlist = os.environ.get("HEARTBEAT_WATCHLIST", DEFAULT_WATCHLIST).split()
    hours = float(os.environ.get("HEARTBEAT_WINDOW_HOURS", "24"))
    model = os.environ.get("HEARTBEAT_MODEL", "gpt-4o-mini")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "")
    home = Path(os.environ.get("HEARTBEAT_HOME",
                               Path.home() / "fingpt" / "heartbeat"))
    digests = home / "digests"
    digests.mkdir(parents=True, exist_ok=True)
    state_path = home / "state.json"

    import fcntl
    # Closing the handle releases the flock, so the with-block must span
    # the whole beat; the lock is held until main() returns.
    with (home / ".lock").open("w") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("ERROR another heartbeat run is already in progress — exiting")
            return 3

        now = time.time()
        date = _utc_date(now)
        log(f"heartbeat v{VERSION} start (dry_run={dry_run}, "
            f"watchlist={' '.join(watchlist)})")

        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except json.JSONDecodeError:
                log("WARN state.json unreadable — starting fresh")

        story_lists = fetch_all(watchlist)
        if not any(story_lists):
            # all feeds dead ≠ no news: fail loudly so systemd records a failure
            # (e.g. the Persistent catch-up beat firing before the network is up)
            log("ERROR every feed failed or returned nothing — aborting beat")
            return 2
        merged = merge_stories(story_lists)
        log(f"merged: {len(merged)} unique stories")
        fresh = filter_window(merged, now=now, hours=hours, seen=state)
        log(f"in window and unseen: {len(fresh)}")
        if not fresh:
            log("nothing new — no digest today")
            return 0

        ranked = rank_stories(fresh, watchlist=set(watchlist), now=now)
        ranked = collapse_near_dups(ranked)
        candidates = ranked[:CANDIDATE_CAP]
        # enrich only stories that can reach the digest; the slice shares dicts
        # with `ranked`, so the jsonl log still sees the enriched text
        enrich_descriptions(candidates)

        digest = None
        if api_key:
            log(f"summarizing {len(candidates)} candidates via {model}")
            digest = llm_digest(candidates, api_key, model, base_url, now)
        else:
            log("no OPENAI_API_KEY — using extractive digest")
        if digest is None:
            digest = extractive_digest(candidates, now=now)

        idx = {s["guid"]: s for s in ranked}
        markdown = render_markdown(digest, idx, now=now)
        stem = date
        if (digests / f"digest-{date}.md").exists():
            # a second same-day beat must never clobber the morning digest
            suffix = datetime.fromtimestamp(now, timezone.utc).strftime("%H%M%S")
            stem = f"{date}-{suffix}"
            log(f"WARN second beat today — writing supplemental digest-{stem}.md")
        md_path = digests / f"digest-{stem}.md"
        jsonl_path = digests / f"items-{stem}.jsonl"
        md_path.write_text(markdown, encoding="utf-8")
        write_jsonl_atomic(jsonl_path, ranked)
        log(f"digest written: {md_path}")
        prune_old_digests(digests, now)

        if dry_run:
            log("dry run — state untouched, skipping Discord post")
            return 0

        # persist state atomically BEFORE delivery: a Discord outage must not
        # cause tomorrow's beat to double-post today's stories
        for guid in compute_seen_updates(merged, fresh, now, hours):
            state.setdefault(guid, now)
        tmp_path = state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(prune_state(state, now)))
        os.replace(tmp_path, state_path)

        if token and channel_id:
            try:
                post_discord(discord_messages(digest, idx), token, channel_id)
            except (urllib.error.URLError, OSError) as exc:
                log(f"ERROR Discord delivery failed after retries: {exc}")
                return 1
        else:
            log("WARN live mode but DISCORD_BOT_TOKEN/DISCORD_CHANNEL_ID missing")
        log("heartbeat done")
        return 0


if __name__ == "__main__":
    sys.exit(main())
