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
    return f'"{artifact["generated_at"]}|{artifact.get("source_items", "")}|{tickers}"'


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
    return (datetime.fromisoformat(artifact["generated_at"])
            if artifact else None)


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
