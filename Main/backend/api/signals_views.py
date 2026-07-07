"""Read-only public endpoint for the latest news-signals artifact.

Contract: signals spec §4.4 (Docs/superpowers/specs/
2026-07-06-news-to-signals-pipeline-design.md). Serves the newest
signals-*.json (newest by mtime, filename as a deterministic tiebreak —
same-day supplemental stems sort lexicographically before the date-only
stem, so stem order alone is not recency) from settings.SIGNALS_DIR, minus
generator/model/prompt_version, plus server-computed staleness_hours.
Every failure path is a 404 {"error": "no_signals"} — never a 500 leaking
pipeline state.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import condition, require_http_methods
from django_ratelimit import ALL
from django_ratelimit.decorators import ratelimit

logger = logging.getLogger(__name__)

_PUBLIC_STRIP = ("generator", "model", "prompt_version")


def _load_latest():
    configured = getattr(settings, "SIGNALS_DIR", "")
    if not configured:
        return None
    directory = Path(configured)
    if not directory.is_dir():
        return None
    candidates = list(directory.glob("signals-*.json"))
    if not candidates:
        return None
    newest = None
    try:
        # Newest by mtime, filename as a deterministic tiebreak: same-day
        # supplemental stems (items-<date>-<HHMMSS>.jsonl ->
        # signals-<date>-<HHMMSS>.json) sort lexicographically BEFORE the
        # date-only stem ("." > "-" in ASCII), so stem order alone is not
        # recency — a same-day re-run would otherwise serve the stale
        # artifact. stat() stays inside the try: a file pruned between
        # glob() and stat() fails closed, never 500s.
        newest = max(candidates, key=lambda p: (p.stat().st_mtime, p.name))
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
    before the view body runs, and all three need the artifact."""
    if not hasattr(request, "_signals_artifact"):
        request._signals_artifact = _load_latest()
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
@require_http_methods(["GET"])
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT,
           method=ALL, block=True)
@condition(etag_func=_etag, last_modified_func=_last_modified)
def news_signals(request: HttpRequest) -> JsonResponse:
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
