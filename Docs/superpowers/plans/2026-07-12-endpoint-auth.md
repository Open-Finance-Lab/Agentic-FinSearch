# Endpoint Bearer-Auth (`finsearch-endpoint-auth-01`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require `Authorization: Bearer <FINGPT_API_KEY>` on the non-`/v1` FinSearch HTTP endpoints (fail-closed in prod, dev unchanged), shipped in a client-safe order so no live client breaks.

**Architecture:** Extract the existing `_authenticate_request` logic (`api/openai_views.py:85`) into a shared `api/auth.py` module exposing both a callable and a `@require_bearer_auth` view decorator; apply the decorator to routes in an order gated by client readiness. Machine-to-machine routes (`api/signals/news/`, consumed by ATL) gate first with zero breakage; the publicly-distributed Chrome extension is taught to send a header first, and only then are its 15 routes gated.

**Tech Stack:** Django 6, `django-ratelimit`, `pytest` (via `uv`), a Manifest-V3 Chrome extension (`Main/frontend`, plain `fetch`), the Concierge `aiohttp` bot.

## Global Constraints

- `_authenticate_request` semantics are preserved verbatim: no `FINGPT_API_KEY` set + `REQUIRE_FINGPT_API_KEY` False ⇒ open (dev); no key + `REQUIRE_FINGPT_API_KEY` True ⇒ 503 fail-closed; key set ⇒ require `Authorization: Bearer <key>` compared with `hmac.compare_digest`, else 401. (`api/openai_views.py:85-127`, `django_config/settings.py:179-180`, `settings_prod.py:71`.)
- `health/` is **exempt** (deploy gate — `entrypoint.sh`/Deploy health probe must stay unauthenticated). Exemption listed + justified in the PR.
- `api/axioms/xbrl/<filename>/` is **exempt** (DECISION 2026-07-12): it is downloaded by a plain `<a download>` browser click (`Main/frontend/src/modules/helpers.js:321`) that cannot attach a header. It stays behind rate-limiting + the opaque server-chosen filename; note the exemption in the PR and docs.
- Auth is **additive**: rate limiting (`@ratelimit`) and cookie-rooted session isolation stay in place.
- Honor the identity seam (`api/identity.py`) — do not hardcode a second auth mechanism; the shared key is a coarse gate, per-user attribution layers on when the login system lands.
- Extension key posture (DECISION 2026-07-12): the extension is **publicly distributed**, so any key it ships is extractable. The header is therefore a **coarse gate** (raises the bar against drive-by API abuse), NOT a real per-user boundary. Label it as such in code comments and docs; the real fix is deferred to the future login/identity system.
- Match existing test style: `django.test.TestCase` / pytest in `Main/backend/tests/`, extending `tests/test_api_auth.py`. Backend check: `cd Main/backend && uv run pytest`.

---

## Rollout ordering (why the phases are ordered this way)

Prod already sets `FINGPT_API_KEY` + `REQUIRE_FINGPT_API_KEY=True` (for `/v1`). So the moment a route gets the decorator, prod enforces it on the next deploy. A publicly-distributed extension **cannot be updated atomically** across all installs, so gating its routes before the header-sending extension build has propagated would 401 every live user. Therefore:

- **Phase 1** gates only `api/signals/news/` (no in-repo caller; ATL's adapter is next-phase and will be built with the header). Zero client breakage. Fully testable headless.
- **Phase 2** ships the extension change that *sends* the header (harmless while its routes are still open).
- **Phase 3** flips the 15 extension routes to gated — only after the Phase 2 build has propagated — plus Concierge env + docs.

Phase 1 is safe to execute now. Phases 2–3 require FlyM1ss to confirm (a) the extension key-delivery mechanism (baked build-time key vs. settings-field paste) and (b) the propagation-window cutover before merge, because they are outward-facing and partly-breaking.

---

## File Structure

- `Main/backend/api/auth.py` — **new.** Shared `authenticate_request(request) -> Optional[JsonResponse]` (moved from `openai_views.py`) + `require_bearer_auth` view decorator. Single source of truth for bearer auth.
- `Main/backend/api/openai_views.py` — **modify.** Delete the local `_authenticate_request`; import from `api.auth` (keep a module-level alias so existing in-body calls and tests keep working).
- `Main/backend/api/signals_views.py` — **modify.** Add `@require_bearer_auth` to `news_signals`.
- `Main/backend/api/views.py` — **modify (Phase 3).** Add `@require_bearer_auth` to the 15 extension-facing views; leave `health` and `xbrl_filing_download` undecorated.
- `Main/backend/tests/test_api_auth.py` — **modify.** Add allow/deny cases per route group (signals in Phase 1; extension groups in Phase 3).
- `Main/frontend/src/modules/backendConfig.js` (+ a new tiny `authHeader` helper) — **modify (Phase 2).** Central place to read the key and build the header.
- `Main/frontend/src/modules/api.js`, `config.js`, `components/link_manager.js` — **modify (Phase 2).** Route all `fetch` calls through a shared helper that attaches the header.
- `Concierge/.env.concierge.example` — **modify (Phase 3).** Un-stale the `FINGPT_API_KEY` comment.
- `Docs/source/api_reference.rst`, `Main/README.md` — **modify (Phase 3).** Update the Authentication notes + endpoint tables.

---

## Phase 1 — Backend auth foundation + gate `signals/news` (non-breaking, execute now)

### Task 1: Extract shared auth module

**Files:**
- Create: `Main/backend/api/auth.py`
- Modify: `Main/backend/api/openai_views.py:85-127` (remove local def, import instead)
- Test: `Main/backend/tests/test_api_auth.py`

**Interfaces:**
- Produces: `authenticate_request(request: HttpRequest) -> Optional[JsonResponse]` (identical behavior to today's `_authenticate_request`); `require_bearer_auth(view_func)` decorator returning the auth `JsonResponse` (401/503) when auth fails, else calling the view. Decorator preserves `functools.wraps`.

- [ ] **Step 1: Write the failing test** — assert the module exists and the decorator gates.

```python
# tests/test_api_auth.py (add)
from django.test import RequestFactory, override_settings
from django.http import JsonResponse
from api.auth import authenticate_request, require_bearer_auth

@require_bearer_auth
def _probe(request):
    return JsonResponse({"ok": True})

@override_settings(REQUIRE_FINGPT_API_KEY=False)
def test_decorator_open_when_no_key(monkeypatch):
    monkeypatch.delenv("FINGPT_API_KEY", raising=False)
    resp = _probe(RequestFactory().get("/x/"))
    assert resp.status_code == 200

def test_decorator_401_when_key_set_and_header_missing(monkeypatch):
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    resp = _probe(RequestFactory().get("/x/"))
    assert resp.status_code == 401

def test_decorator_200_when_bearer_matches(monkeypatch):
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    resp = _probe(RequestFactory().get("/x/", HTTP_AUTHORIZATION="Bearer sekret"))
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify it fails** — `cd Main/backend && uv run pytest tests/test_api_auth.py -k decorator -v` → FAIL (`ModuleNotFoundError: api.auth`).

- [ ] **Step 3: Create `api/auth.py`** — move the body of `_authenticate_request` verbatim into `authenticate_request`, add the decorator:

```python
"""Shared bearer-token auth for FinSearch HTTP endpoints.

Single source of truth extracted from openai_views so /v1 and the non-/v1
routes cannot drift. See Docs/source/api_reference.rst (Authentication) and
django_config/settings.py:179-180. The shared FINGPT_API_KEY is a coarse gate;
per-user attribution is deferred to the identity seam (api/identity.py)."""
import functools
import hmac
import logging
import os
from typing import Optional

from django.conf import settings
from django.http import HttpRequest, JsonResponse

logger = logging.getLogger(__name__)


def authenticate_request(request: HttpRequest) -> Optional[JsonResponse]:
    api_key = os.getenv('FINGPT_API_KEY')
    if not api_key:
        if getattr(settings, 'REQUIRE_FINGPT_API_KEY', False):
            logger.error(
                "FINGPT_API_KEY is not set but REQUIRE_FINGPT_API_KEY is True; "
                "refusing request (fail closed)."
            )
            return JsonResponse(
                {'error': {'message': 'Server authentication is misconfigured.', 'type': 'server_error'}},
                status=503,
            )
        return None
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header:
        return JsonResponse(
            {'error': {'message': 'Missing Authorization header. Use: Authorization: Bearer <api_key>', 'type': 'authentication_error'}},
            status=401,
        )
    if not auth_header.startswith('Bearer '):
        return JsonResponse(
            {'error': {'message': 'Invalid Authorization format. Use: Authorization: Bearer <api_key>', 'type': 'authentication_error'}},
            status=401,
        )
    if not hmac.compare_digest(auth_header[7:], api_key):
        return JsonResponse(
            {'error': {'message': 'Invalid API key', 'type': 'authentication_error'}},
            status=401,
        )
    return None


def require_bearer_auth(view_func):
    """Gate a view with authenticate_request. Placed as the OUTERMOST decorator
    (just under @csrf_exempt) so an unauthorized request is rejected before any
    rate-limit token, disk load, or agent work — a deliberate improvement over
    the /v1 in-body check, same policy."""
    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        auth_error = authenticate_request(request)
        if auth_error is not None:
            return auth_error
        return view_func(request, *args, **kwargs)
    return _wrapped
```

- [ ] **Step 4: Rewire `openai_views.py`** — replace the local `_authenticate_request` def with `from api.auth import authenticate_request as _authenticate_request` (keeps the two in-body call sites at `:161,211` and any test import working unchanged).

- [ ] **Step 5: Run** — `cd Main/backend && uv run pytest tests/test_api_auth.py -v` and `uv run pytest tests/test_openai_api.py -v` → PASS (existing /v1 auth tests still green through the alias).

- [ ] **Step 6: Commit** — `git add Main/backend/api/auth.py Main/backend/api/openai_views.py Main/backend/tests/test_api_auth.py && git commit` (message per Task 3).

### Task 2: Gate `api/signals/news/`

**Files:**
- Modify: `Main/backend/api/signals_views.py:153-158` (decorator stack on `news_signals`)
- Test: `Main/backend/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `require_bearer_auth` from Task 1.

- [ ] **Step 1: Write failing tests** — signals allow/deny:

```python
# tests/test_api_auth.py (add)
from django.test import Client, override_settings

def test_signals_401_when_key_set_no_header(monkeypatch):
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    assert Client().get("/api/signals/news/").status_code == 401

@override_settings(REQUIRE_FINGPT_API_KEY=False)
def test_signals_open_when_no_key(monkeypatch):
    monkeypatch.delenv("FINGPT_API_KEY", raising=False)
    # 404 no_signals (empty SIGNALS_DIR) proves it reached the view, not 401
    assert Client().get("/api/signals/news/").status_code in (200, 404)
```

- [ ] **Step 2: Run to verify** the 401 test fails → currently returns 404/200 (no auth). `uv run pytest tests/test_api_auth.py -k signals -v`.

- [ ] **Step 3: Add the decorator** — top of the `news_signals` stack, directly under `@csrf_exempt`:

```python
@csrf_exempt
@require_bearer_auth
@require_http_methods(["GET"])
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT, method=ALL, block=True)
@condition(etag_func=_etag, last_modified_func=_last_modified)
def news_signals(request: HttpRequest) -> JsonResponse:
```
(Import: `from api.auth import require_bearer_auth`.) Placement above `@condition` means an unauthorized request never triggers the `_load_artifact` disk read.

- [ ] **Step 4: Run** — `uv run pytest tests/test_api_auth.py -k signals -v` → PASS.

- [ ] **Step 5: Full backend suite** — `cd Main/backend && uv run pytest` → all green (confirms no regression to signals ETag/conditional tests or the wider suite).

### Task 3: Commit + PR (Phase 1)

- [ ] **Step 1: Commit** (if not already, from Tasks 1–2):

```
feat(api): extend bearer auth to signals/news (F: endpoint-auth-01 P1)

Extract _authenticate_request into api/auth.py (shared callable +
require_bearer_auth decorator); gate api/signals/news/ — the ATL-facing,
previously-open machine endpoint (no in-repo caller). Extension/Concierge
routes gate in a later, client-coordinated phase. health/ + xbrl download
stay exempt. Dev-open / prod-fail-closed semantics unchanged.
```

- [ ] **Step 2: Push + open PR**, title `feat(api): bearer-auth on signals/news (P1)`; body lists the health + xbrl exemptions with justification and the Phase-2/3 sequencing.

- [ ] **Step 3: `/code-review` the diff; fix findings in-PR; merge when green.**

---

## Phase 2 — Extension sends the header (outward-facing; CONFIRM key delivery before merge)

> **DECISION REQUIRED before executing:** key delivery mechanism.
> - **Option A — baked build-time key:** inject `FINGPT_API_KEY` at extension build time; every install ships the same coarse-gate key, transparent to users. Simplest UX; extractable (accepted — coarse gate).
> - **Option B — settings-field paste:** add a key field to `settings_window.js` stored in `chrome.storage.local`; nothing works until a user pastes a key. Aligns with future per-user auth; heavy UX friction for a public extension.
> The plan below is written for **Option A** with a settings-field override left as a follow-on; adjust Task 5 if Option B is chosen.

### Task 4: Central auth-header helper + config

**Files:**
- Modify: `Main/frontend/src/modules/backendConfig.js` (add `getAuthHeaders()`)
- Test: `Main/frontend/` test harness if present, else manual (documented).

- [ ] **Step 1:** Add a build-time constant read (e.g. `__FINGPT_API_KEY__` replaced by the bundler / a `config`-injected global) and:

```js
// backendConfig.js
export function getAuthHeaders() {
  // Coarse gate only: a public-extension key is extractable. Real per-user
  // auth is deferred to the backend login system (api/identity.py).
  const key = (typeof window !== 'undefined' && window.FINGPT_API_KEY) || '';
  return key ? { 'Authorization': `Bearer ${key}` } : {};
}
```

- [ ] **Step 2:** Document the build-time key injection point (where the CI/build sets `window.FINGPT_API_KEY`), matching how `AGENTIC_BACKEND_URL` is injected.

### Task 5: Route all `fetch` calls through the header helper

**Files:**
- Modify: `Main/frontend/src/modules/api.js` (10 call sites incl. streams at `:104,159,298,375,405,422,450,500,531,549`), `config.js:14`, `components/link_manager.js:50,179`.

- [ ] **Step 1:** Merge `...getAuthHeaders()` into every backend `fetch` options `headers` object (GET, POST-JSON, and the SSE `ReadableStream` POSTs). Preserve existing `credentials: 'include'` and `Content-Type`.
- [ ] **Step 2:** Leave the `xbrl` `<a download>` in `helpers.js:321` untouched (route is exempt).
- [ ] **Step 3:** Rebuild `Main/frontend/dist`; verify the header appears on requests (DevTools network / a local backend with a key set).
- [ ] **Step 4:** Commit + PR: `feat(ext): attach bearer header to backend calls`. Ship + let the built extension propagate BEFORE Phase 3.

---

## Phase 3 — Gate extension routes + Concierge + docs (execute after Phase 2 propagates)

### Task 6: Gate the 15 extension views

**Files:** Modify `Main/backend/api/views.py` — add `@require_bearer_auth` (outermost, under `@csrf_exempt`) to: `add_webtext`, `auto_scrape`, `chat_response`, `chat_response_stream`, `adv_response`, `adv_response_stream`, `get_sources`, `clear`, `get_preferred_urls`, `sync_preferred_urls`, `log_question`, `get_available_models`, `validate_claims`, `has_axiom_claims`. **Do NOT** decorate `health` or `xbrl_filing_download`.
- [ ] Add allow/deny tests per group (chat, context/prefs, axioms) in `tests/test_api_auth.py`; RED→GREEN; full suite green.

### Task 7: Concierge env + docs

- [ ] `Concierge/.env.concierge.example:11-12` — replace "Usually unset — the extension endpoints aren't Bearer-gated." with a note that `FINGPT_API_KEY` is now REQUIRED to reach the (Bearer-gated) `get_chat_response_stream/`; keep the var line uncommented-with-placeholder.
- [ ] `Docs/source/api_reference.rst:49-53` + `:495-503` — rewrite the "do not currently require an API key"/"unauthenticated" notes to state bearer auth is now required (except `health/` + the `xbrl` download); add an Auth row to the extension endpoint table (`:505-559`) and `-H "Authorization: Bearer $API_KEY"` to the extension cURL examples. Sphinx builds with zero warnings (`cd Main/backend && uv run --group docs sphinx-build -W ...`).
- [ ] `Main/README.md:99-109` + `:350-365` — add auth info to the extension endpoint lists/tables.

### Task 8: E2E verification (spec acceptance)

- [ ] With a key-enabled local backend + the Phase-2 extension build: verify chat (thinking + research, both streams), auto-scrape, preferred links, validate — all succeed with the header; a request with a wrong/no key returns 401. Record the manual result in the PR (headless CI cannot load the extension).

---

## Self-Review (against the spec)

- Spec req 1 (all non-/v1 require bearer when key set): Tasks 2 (signals) + 6 (extension). ✅ (health + xbrl exemptions justified per Global Constraints.)
- Spec req 2 (dev open): preserved verbatim in Task 1. ✅
- Spec req 3 (prod 503 fail-closed): preserved in `authenticate_request`. ✅
- Spec req 4 (extension keeps working / sends header): Phase 2. ✅ (coarse-gate caveat documented.)
- Spec req 5 (api_reference.rst updated): Task 7. ✅
- Spec req 6 (allow + deny tests): Tasks 1, 2, 6. ✅
- Extra vs spec: Concierge (Task 7) + xbrl exemption (Global Constraints) + the client-safe rollout ordering — all surfaced by the 2026-07-12 mapping.
- Open decisions (flagged, not placeholders): Phase-2 key-delivery (A vs B) and the Phase-3 propagation cutover — both outward-facing, require FlyM1ss sign-off before merge.
