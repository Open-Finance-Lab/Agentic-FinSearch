"""Read-only public endpoint for the latest news-signals artifact.

Contract: signals spec §4.4 (Docs/superpowers/specs/
2026-07-06-news-to-signals-pipeline-design.md). Serves the newest
signals-*.json (newest by mtime, filename as a deterministic tiebreak —
same-day supplemental stems sort lexicographically before the date-only
stem, so stem order alone is not recency) from settings.SIGNALS_DIR, minus
generator/model/prompt_version, plus server-computed staleness_hours.
An optional ?as_of=YYYY-MM-DD selects the newest artifact dated on or before
that day (point-in-time — no lookahead, robust to gaps); a malformed value is
a 400 {"error": "bad_as_of"}.
Every failure path is a 404 {"error": "no_signals"} — never a 500 leaking
pipeline state.
"""
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import condition, require_http_methods
from django_ratelimit import ALL
from django_ratelimit.decorators import ratelimit

from api.auth import require_bearer_auth

logger = logging.getLogger(__name__)

_PUBLIC_STRIP = ("generator", "model", "prompt_version")

_SIGNALS_WIRE_SCHEMA_VERSION = 2  # wire is always v2; v1 disk artifacts are normalized below


def _normalize_legacy_signal_entry(entry):
    """v1 artifacts predate the score->sentiment_score rename; rename at the
    boundary so `score` never reaches the wire, whether the artifact is read
    as latest or via ?as_of. `sentiment_score` wins if an entry somehow carries
    both — the v2 name is authoritative, and the legacy key is dropped rather
    than allowed to overwrite it. Copies — the top-level body is fresh, but
    entry dicts are shared references into the loaded artifact and must not be
    mutated. Non-dict entries pass through untouched (defensive)."""
    if not isinstance(entry, dict) or "score" not in entry:
        return entry
    entry = dict(entry)
    legacy = entry.pop("score")
    entry.setdefault("sentiment_score", legacy)
    return entry


_AS_OF_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


def _as_of(request: HttpRequest):
    """Parse ?as_of=YYYY-MM-DD. Returns None (param absent) or a datetime.date.
    Raises ValueError on any malformed value — the view maps that to a 400."""
    raw = request.GET.get("as_of")
    if raw is None:
        return None
    if not _AS_OF_RE.match(raw):
        raise ValueError(f"as_of must be YYYY-MM-DD, got {raw!r}")
    return date.fromisoformat(raw)  # also rejects impossible dates (e.g. 2026-13-40)


def _stem_date(path: Path):
    """The leading YYYY-MM-DD of a signals-<...>.json filename, or None for a
    non-dated stem (e.g. signals-a.json). ?as_of resolves by the date a batch
    is *for* (its filename stem), reusing the (mtime, name) tiebreak below for
    same-day supplemental reruns."""
    head = path.name[len("signals-"):len("signals-") + 10]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def _load_artifact(as_of=None):
    configured = getattr(settings, "SIGNALS_DIR", "")
    if not configured:
        return None
    directory = Path(configured)
    if not directory.is_dir():
        return None
    candidates = list(directory.glob("signals-*.json"))
    newest = None
    try:
        if as_of is not None:
            # Point-in-time: keep only artifacts whose batch date is on or
            # before as_of. Non-dated stems (_stem_date is None) are skipped.
            # Inside the try so a bad as_of argument fails closed (None ->
            # 404), never a 500 — the module contract.
            candidates = [p for p in candidates
                          if (d := _stem_date(p)) is not None and d <= as_of]
        if not candidates:
            return None
        # stat() stays inside the try in both branches: a file pruned between
        # glob() and stat() fails closed, never 500s.
        if as_of is None:
            newest = max(candidates, key=lambda p: (p.stat().st_mtime, p.name))
        else:
            # Calendar order first: a backfilled/reprocessed older-day
            # artifact (rewritten in place -> fresh mtime) must never outrank
            # a newer-dated artifact inside the as_of window; (mtime, name)
            # stays the same-day-supplemental tiebreak. Every candidate here
            # has a non-None stem date (filtered above).
            newest = max(candidates, key=lambda p: (
                _stem_date(p), p.stat().st_mtime, p.name))
        artifact = json.loads(newest.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(artifact["generated_at"])
        if generated.tzinfo is None:
            # fromisoformat accepts naive strings; the view subtracts this
            # from an aware now() for staleness_hours
            raise ValueError("generated_at must be timezone-aware")
        if not isinstance(artifact["signals"], dict):
            raise ValueError("signals must be a JSON object")
        return artifact
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("signals: unreadable artifact %s: %s",
                     newest.name if newest else "<vanished>", exc)
        return None  # fail closed: unreadable == no signals


def _get_artifact(request: HttpRequest):
    """One disk load per request: @condition calls _etag and _last_modified
    before the view body runs, and all three need the artifact. A malformed
    ?as_of resolves to None here so the conditional validators never raise;
    the view re-parses and returns 400."""
    if not hasattr(request, "_signals_artifact"):
        try:
            as_of = _as_of(request)
        except ValueError:
            request._signals_artifact = None
        else:
            request._signals_artifact = _load_artifact(as_of)
    return request._signals_artifact


def _tickers_filter(request: HttpRequest):
    """Normalized tickers filter (deduped, uppercased, sorted) — the single
    definition shared by the ETag variant key and the view's filtering."""
    raw = request.GET.get("tickers") or ""
    return sorted({t.strip().upper() for t in raw.split(",") if t.strip()})


def _etag(request: HttpRequest):
    artifact = _get_artifact(request)
    if artifact is None:
        return None
    # Each token percent-encoded so the "+" join is unambiguous (a literal
    # "+" inside a token — reachable via %2B — must not collide with the
    # separator), and "+"-joined rather than ","-joined because Django's
    # parse_etags() rejects an ETag containing a comma (HTTP list separator).
    tickers = "+".join(quote(t, safe="") for t in _tickers_filter(request))
    # Salted with the wire schema version: the artifact on disk can be
    # byte-identical across a wire-shape change (e.g. this rename's legacy
    # normalizer), which would otherwise leave the ETag unchanged while the
    # served body changes — a conditional request could then 304 a client
    # into a stale body. The salt guarantees a wire-shape bump invalidates
    # every cached ETag, including ?as_of reads of artifacts that never
    # change again.
    return (f'"v{_SIGNALS_WIRE_SCHEMA_VERSION}|{artifact["generated_at"]}'
            f'|{artifact.get("source_items", "")}|{tickers}"')


def _last_modified(request: HttpRequest):
    # Only the unfiltered variant carries Last-Modified: generated_at is
    # identical across every tickers variant of one artifact, and a client
    # revalidating a filtered request with If-Modified-Since alone (legal —
    # and without If-None-Match Django never consults the ETag) would get a
    # 304 telling it to reuse a differently-filtered cached body. The ETag
    # is the only validator that can carry the variant.
    if _tickers_filter(request):
        return None
    artifact = _get_artifact(request)
    if artifact is None:
        return None
    # A pre-v2 artifact's *representation* changes at the wire boundary while
    # its generated_at does not, and Last-Modified cannot express that (Django
    # skips the ETag entirely when If-None-Match is absent — get_conditional_
    # response step 4), so an IMS-only revalidation would 304 a client into a
    # stale v1 body. Artifacts the normalizer rewrites don't get the validator;
    # native-v2 artifacts keep it. Self-retiring once every artifact is v2.
    if artifact.get("schema_version") != _SIGNALS_WIRE_SCHEMA_VERSION:
        return None
    return datetime.fromisoformat(artifact["generated_at"])


@csrf_exempt
@require_bearer_auth
@require_http_methods(["GET"])
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT,
           method=ALL, block=True)
@condition(etag_func=_etag, last_modified_func=_last_modified)
def news_signals(request: HttpRequest) -> JsonResponse:
    try:
        _as_of(request)
    except ValueError:
        return JsonResponse({'error': 'bad_as_of'}, status=400)
    artifact = _get_artifact(request)
    if artifact is None:
        return JsonResponse({'error': 'no_signals'}, status=404)
    body = {k: v for k, v in artifact.items() if k not in _PUBLIC_STRIP}
    body["signals"] = {t: _normalize_legacy_signal_entry(e)
                       for t, e in (body.get("signals") or {}).items()}
    if body.get("schema_version") != _SIGNALS_WIRE_SCHEMA_VERSION:
        body["schema_version"] = _SIGNALS_WIRE_SCHEMA_VERSION
    generated = datetime.fromisoformat(artifact["generated_at"])
    now = datetime.now(timezone.utc)
    body["staleness_hours"] = round(
        (now - generated).total_seconds() / 3600, 1)
    wanted = set(_tickers_filter(request))
    if wanted:
        body["signals"] = {k: v for k, v in body["signals"].items()
                           if k in wanted}
    response = JsonResponse(body)
    response["Cache-Control"] = "public, max-age=300"
    return response


# --- Raw news-items endpoint (ATL integration Phase B) ---------------------
# Sibling of news_signals, but reads items-*.jsonl batches directly with no
# subject/roundup/LLM gating (news_signals' select_candidates etc. is dropped
# entirely — this serves the raw, ungated feed). The validation/sanitization
# below is PORTED byte-faithfully from Heartbeat/news_signals.py
# (validation_gate/clean_text) — that script is deliberately isolated and not
# importable from the Django app — because it is a security trust boundary
# (spec §7.1): do not paraphrase it. Heartbeat/tests/test_port_parity.py is the
# anti-drift guard: it pins the constants exactly, and compares the two copies'
# BEHAVIOR over a corpus — so it catches one-sided edits only on inputs the
# corpus reaches. It shipped missing the malformed-numeric case and went green
# over exactly that drift once. Widen the corpus when you add a branch here;
# edit both copies, in the same commit.
# The ONE deliberate divergence: cap resolution. Heartbeat reads
# SIGNALS_MAX_FILE_MB in load_config; here the same env var arrives via
# settings.RAW_ITEMS_MAX_FILE_MB (one operator knob, two readers). The parity
# test pins the two defaults together.
REQUIRED_FIELDS = ("guid", "title", "link", "source", "published", "editorial_score")
FIELD_CAPS = {"title": 500, "description": 5000, "link": 2000, "source": 200, "guid": 200}
# Required fields that must be strings. A malformed type drops the story — same
# stance as the numeric parse in _validate_items — so a corrupt field can never
# reach _clean_text as a non-str and can never poison the whole batch.
TEXT_REQUIRED_FIELDS = ("guid", "title", "link", "source")

# Control chars + bidi/direction overrides (recency/spoofing hygiene, spec 7.1).
_CONTROL_RE = re.compile(
    "[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f"
    "\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]"
)
_LINEBREAK_RE = re.compile("[\\t\\n\\v\\f\\r\\x1c-\\x1e\\x85\\u2028\\u2029]+")


def _clean_text(s, cap):
    # non-str (incl. None) collapses to "": the gate must never raise on a
    # malformed field type. Required-field types are checked in _validate_items;
    # this keeps optional and LLM-derived callers total on their own.
    s = s if isinstance(s, str) else ""
    # line boundaries first — _CONTROL_RE would strip \v/\f/\x1c-\x1e to nothing and fuse words
    s = _LINEBREAK_RE.sub(" ", unicodedata.normalize("NFC", s))
    s = _CONTROL_RE.sub("", s)
    s = s.replace("NEWS_DATA", "")   # datamarking token can never come from the feed
    return s[:cap]


_MAX_ITEMS_FILE_MB = 10   # default only; settings.RAW_ITEMS_MAX_FILE_MB is the live value


def _validate_items(path, max_file_mb=None):
    """Input trust boundary (ported from Heartbeat's validation_gate).
    Batch-level defects raise ValueError (poison pill); a bad `published`, a
    malformed numeric, or a non-str TEXT_REQUIRED_FIELDS value drops only that
    story."""
    if max_file_mb is None:
        max_file_mb = getattr(settings, "RAW_ITEMS_MAX_FILE_MB", _MAX_ITEMS_FILE_MB)
    st = path.stat()
    if st.st_size > max_file_mb * 1024 * 1024:
        raise ValueError(f"file exceeds {max_file_mb}MB")
    lo, hi = st.st_mtime - 30 * 86400, st.st_mtime + 3600
    stories = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        story = json.loads(line)                       # JSONDecodeError -> ValueError -> poison pill
        for field in REQUIRED_FIELDS:
            if field not in story:
                raise ValueError(f"line {i}: missing required field {field}")
        try:
            published = float(story["published"])
            story["editorial_score"] = float(story["editorial_score"])
        except (TypeError, ValueError):
            continue                                   # malformed numerics: drop story, keep batch
        if not (lo <= published <= hi):
            continue                                   # forged/insane epoch: drop story, keep batch
        if not all(isinstance(story[f], str) for f in TEXT_REQUIRED_FIELDS):
            continue  # malformed text types: drop the story, keep the batch
        story["published"] = published
        story["title"] = _clean_text(story["title"], FIELD_CAPS["title"])
        story["description"] = _clean_text(story.get("description", ""), FIELD_CAPS["description"])
        story["source"] = _clean_text(story["source"], FIELD_CAPS["source"])
        story["guid"] = _clean_text(story["guid"], FIELD_CAPS["guid"])
        story["link"] = _clean_text(story["link"], FIELD_CAPS["link"])
        # non-list/non-string tickers dropped, never crashed: a corrupt
        # "tickers":[123] must not .upper() -> AttributeError, which the only
        # caller (process_batch's ValueError-only except) would not catch and
        # which would abort the whole sweep; a bare "tickers":"AAPL" must not
        # char-iterate to ['A','A','P','L'].
        raw_tickers = story.get("tickers", [])
        story["tickers"] = ([t.upper() for t in raw_tickers if isinstance(t, str)]
                            if isinstance(raw_tickers, list) else [])
        stories.append(story)
    return stories


# The response contract's 8 per-story keys, exactly — extra input keys (e.g.
# "feeds") are dropped at projection time in the view body.
# _ITEMS_CONTRACT_FIELDS names the DISK keys (RSS-native title/link — the
# scraper's format, validated above); _ITEMS_WIRE_RENAMES maps them to the
# news-story vocabulary the wire speaks (headline/url — the nouns the live
# signals endpoint already uses). Disk stays scraper-native; the API boundary
# does the rename. Contract doc: ATL docs/integrations/finsearch-news-items.md.
_ITEMS_CONTRACT_FIELDS = ("guid", "title", "link", "source", "published",
                          "description", "tickers", "editorial_score")
_ITEMS_WIRE_RENAMES = {"title": "headline", "link": "url"}
_ITEMS_SCHEMA_VERSION = 2  # news-story v2: score -> editorial_score (2026-07-14 spec)


def _load_items():
    """-> (newest_path, newest_mtime, stories_sorted_desc) or None. No ?as_of
    for items: always the single newest batch. Mirrors _load_artifact's
    fail-closed contract — any locate/read/validate failure (including a
    batch that vanishes mid-race, or one that validates to zero stories)
    returns None, never falling back to an older batch.
    The mtime is captured here — the only stat() of the winning file — and
    carried through the memo so @condition's etag/last-modified functions
    never re-stat: a prune in the window between this load and @condition's
    call would otherwise turn into a 500 (see _items_etag/_items_last_modified)."""
    configured = getattr(settings, "RAW_ITEMS_DIR", "")
    if not configured:
        return None
    directory = Path(configured)
    if not directory.is_dir():
        return None
    candidates = list(directory.glob("items-*.jsonl"))
    if not candidates:
        return None
    newest = None
    try:
        # stat() stays inside the try: a file pruned between glob() and
        # stat() fails closed, never 500s (same race as _load_artifact).
        # Each candidate is stat()'d exactly once here (for the max() key);
        # the winner's mtime is captured off that same call, not re-stat'd.
        stats = [(p, p.stat().st_mtime) for p in candidates]
        newest, mtime = max(stats, key=lambda ps: (ps[1], ps[0].name))
        stories = _validate_items(newest)
        stories.sort(key=lambda s: s["published"], reverse=True)
        if not stories:
            return None
        return newest, mtime, stories
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("news_items: unreadable batch %s: %s",
                     newest.name if newest else "<vanished>", exc)
        return None  # fail closed: unreadable/empty == no items


def _get_items(request: HttpRequest):
    """One disk load per request: @condition calls the etag/last-modified
    functions before the view body runs, and all three need the loaded
    batch. Mirrors _get_artifact."""
    if not hasattr(request, "_news_items"):
        request._news_items = _load_items()
    return request._news_items


def _parse_limit(request: HttpRequest):
    """Parse ?limit. Absent -> None (caller defaults to 50); present but not
    a base-10 integer -> raises ValueError (the view maps that to a 400);
    otherwise clamped to [1, 200]."""
    raw = request.GET.get("limit")
    if raw is None:
        return None
    return max(1, min(200, int(raw)))


def _items_etag(request: HttpRequest):
    loaded = _get_items(request)
    if loaded is None:
        return None
    try:
        limit = _parse_limit(request)
    except ValueError:
        return None  # bad limit: skip conditional handling, view re-parses -> 400
    path, mtime, _ = loaded
    # mtime comes from the _load_items memo, not a re-stat: @condition calls
    # this before the view body runs, and re-statting here would reopen the
    # TOCTOU window _load_items already closed (a batch pruned in between
    # would 500 instead of 404).
    return f'"{path.name}|{mtime}|{limit or 50}"'


def _items_last_modified(request: HttpRequest):
    # Only the default-limit variant carries Last-Modified: an explicit
    # ?limit slices the batch differently, so only the ETag (which encodes
    # the limit) can identify that variant — same stance as _last_modified's
    # tickers-filter handling above.
    if request.GET.get("limit") is not None:
        return None
    loaded = _get_items(request)
    if loaded is None:
        return None
    _, mtime, _ = loaded
    return datetime.fromtimestamp(mtime, tz=timezone.utc)  # no stat() — see _items_etag


@csrf_exempt
@require_bearer_auth
@require_http_methods(["GET"])
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT,
           method=ALL, block=True)
@condition(etag_func=_items_etag, last_modified_func=_items_last_modified)
def news_items(request: HttpRequest) -> JsonResponse:
    try:
        limit = _parse_limit(request)
    except ValueError:
        return JsonResponse({'error': 'bad_limit'}, status=400)
    loaded = _get_items(request)
    if loaded is None:
        return JsonResponse({'error': 'no_items'}, status=404)
    path, _mtime, stories = loaded
    effective_limit = limit or 50
    sliced = stories[:effective_limit]
    items = [{_ITEMS_WIRE_RENAMES.get(k, k): story[k] for k in _ITEMS_CONTRACT_FIELDS}
             for story in sliced]
    body = {"schema_version": _ITEMS_SCHEMA_VERSION, "items": items,
            "count": len(items), "batch": path.name}
    response = JsonResponse(body)
    response["Cache-Control"] = "public, max-age=300"
    return response
