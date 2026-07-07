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
    candidates = sorted(directory.glob("signals-*.json"))
    if not candidates:
        return None
    # Newest by mtime, filename as a deterministic tiebreak: same-day
    # supplemental stems (items-<date>-<HHMMSS>.jsonl ->
    # signals-<date>-<HHMMSS>.json) sort lexicographically BEFORE the
    # date-only stem ("." > "-" in ASCII), so stem order alone is not
    # recency — a same-day re-run would otherwise serve the stale artifact.
    newest = max(candidates, key=lambda p: (p.stat().st_mtime, p.name))
    try:
        artifact = json.loads(newest.read_text(encoding="utf-8"))
        datetime.fromisoformat(artifact["generated_at"])  # must parse
        if not isinstance(artifact["signals"], dict):
            raise ValueError("signals must be a JSON object")
        return artifact
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("signals: unreadable artifact %s: %s", newest.name, exc)
        return None  # fail closed: unreadable == no signals


def _etag(request: HttpRequest):
    artifact = _load_latest()
    if artifact is None:
        return None
    tickers = (request.GET.get("tickers") or "").upper().replace(" ", "")
    return f'"{artifact["generated_at"]}|{artifact.get("source_items", "")}|{tickers}"'


def _last_modified(request: HttpRequest):
    artifact = _load_latest()
    return (datetime.fromisoformat(artifact["generated_at"])
            if artifact else None)


@csrf_exempt
@require_http_methods(["GET"])
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT,
           method=ALL, block=True)
@condition(etag_func=_etag, last_modified_func=_last_modified)
def news_signals(request: HttpRequest) -> JsonResponse:
    artifact = _load_latest()
    if artifact is None:
        return JsonResponse({'error': 'no_signals'}, status=404)
    body = {k: v for k, v in artifact.items() if k not in _PUBLIC_STRIP}
    generated = datetime.fromisoformat(artifact["generated_at"])
    now = datetime.now(timezone.utc)
    body["staleness_hours"] = round(
        (now - generated).total_seconds() / 3600, 1)
    raw = request.GET.get("tickers")
    if raw:
        wanted = {t.strip().upper() for t in raw.split(",") if t.strip()}
        body["signals"] = {k: v for k, v in body["signals"].items()
                           if k in wanted}
    response = JsonResponse(body)
    response["Cache-Control"] = "public, max-age=300"
    return response
