# Endpoint Bearer-Auth (`finsearch-endpoint-auth-01`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require `Authorization: Bearer <FINGPT_API_KEY>` on the non-`/v1` FinSearch HTTP endpoints (fail-closed in prod, dev unchanged), shipped in a client-safe order so no live client breaks.

**Architecture:** Extract the existing `_authenticate_request` logic (`api/openai_views.py:85`) into a shared `api/auth.py` module exposing both a callable and a `@require_bearer_auth` view decorator; apply the decorator to routes in an order gated by client readiness. Machine-to-machine routes (`api/signals/news/`, consumed by ATL) gate first with zero breakage; the publicly-distributed Chrome extension is taught to send a header first, and only then are its 14 gatable views flipped to enforced.

**Tech Stack:** Django 6, `django-ratelimit`, `pytest` (via `uv`), a Manifest-V3 Chrome extension (`Main/frontend`, built with Bun + webpack 5, plain `fetch`, no central request wrapper), the Concierge `aiohttp` bot.

## Global Constraints

- `_authenticate_request` semantics are preserved verbatim: no `FINGPT_API_KEY` set + `REQUIRE_FINGPT_API_KEY` False ⇒ open (dev); no key + `REQUIRE_FINGPT_API_KEY` True ⇒ 503 fail-closed; key set ⇒ require `Authorization: Bearer <key>` compared with `hmac.compare_digest`, else 401. (`api/openai_views.py:85-127`, `django_config/settings.py:179-180`, `settings_prod.py:71`.)
- `health/` is **exempt** (deploy gate — `entrypoint.sh`/Deploy health probe must stay unauthenticated). Exemption listed + justified in the PR.
- `api/axioms/xbrl/<filename>/` is **exempt** (DECISION 2026-07-12): it is downloaded by a plain `<a download>` browser click (`Main/frontend/src/modules/helpers.js:321`) that cannot attach a header. It stays behind rate-limiting + the opaque server-chosen filename; note the exemption in the PR and docs.
- Auth is **additive**: rate limiting (`@ratelimit`) and cookie-rooted session isolation stay in place.
- Honor the identity seam (`api/identity.py`) — do not hardcode a second auth mechanism; the shared key is a coarse gate, per-user attribution layers on when the login system lands.
- Extension key posture (DECISION 2026-07-12): the extension is **publicly distributed**, so any key it ships is extractable. The header is therefore a **coarse gate** (raises the bar against drive-by API abuse), NOT a real per-user boundary. Label it as such in code comments and docs; the real fix is deferred to the future login/identity system.
- Tests extend `Main/backend/tests/test_api_auth.py`. Add new cases as **module-level pytest functions** (not methods on the existing `SimpleTestCase`) so the `monkeypatch` fixture is injected; `conftest.py` already configures Django for bare pytest. Route-level tests use `django.test.Client` (proves the decorator is wired through `urls.py`); if a `Client` call trips the no-DB harness on session access, fall back to `RequestFactory` + calling the view object directly, as the existing tests do. Backend check: `cd Main/backend && uv run pytest`.

---

## Rollout ordering (why the phases are ordered this way)

Prod already sets `FINGPT_API_KEY` + `REQUIRE_FINGPT_API_KEY=True` (for `/v1`). So the moment a route gets the decorator, prod enforces it on the next deploy. A publicly-distributed extension **cannot be updated atomically** across all installs, so gating its routes before the header-sending extension build has propagated would 401 every live user. Therefore:

- **Phase 1** gates only `api/signals/news/` (no in-repo caller; ATL's adapter is next-phase and will be built with the header). Zero client breakage. Fully testable headless.
- **Phase 2** ships the extension change that *sends* the header (harmless while its routes are still open).
- **Phase 3** flips the 14 gatable extension views to enforced — only after the Phase 2 build has propagated — plus Concierge env + docs.

Phase 1 is safe to execute now. Phases 2–3 are outward-facing and partly-breaking. The extension key-delivery mechanism is **resolved** (Option A — webpack `DefinePlugin`; see Phase 2), so the only remaining sign-off is the **propagation-window cutover** — FlyM1ss confirms it before the Phase 3 merge.

---

## File Structure

- `Main/backend/api/auth.py` — **new.** Shared `authenticate_request(request) -> Optional[JsonResponse]` (moved from `openai_views.py`) + `require_bearer_auth` view decorator. Single source of truth for bearer auth.
- `Main/backend/api/openai_views.py` — **modify.** Delete the local `_authenticate_request`; import from `api.auth` (keep a module-level alias so existing in-body calls and tests keep working).
- `Main/backend/api/signals_views.py` — **modify.** Add `@require_bearer_auth` to `news_signals`.
- `Main/backend/api/views.py` — **modify (Phase 3).** Add `@require_bearer_auth` to the 14 extension-facing views; leave `health` and `xbrl_filing_download` undecorated.
- `Main/backend/tests/test_api_auth.py` — **modify.** Add allow/deny cases per route group (signals in Phase 1; extension groups in Phase 3).
- `Main/frontend/webpack.config.js` — **modify (Phase 2).** Add a `webpack.DefinePlugin` that bakes `process.env.FINGPT_API_KEY` into the bundle at build time (the seam does not exist today).
- `Main/frontend/src/modules/backendConfig.js` — **modify (Phase 2).** Add `getAuthHeaders()` + the build-time coarse-gate key constant next to `DEFAULT_BACKEND_BASE_URL`.
- `Main/frontend/src/modules/api.js`, `config.js`, `components/link_manager.js` — **modify (Phase 2).** Spread `...getAuthHeaders()` into all 13 backend `fetch` header objects (no central wrapper exists; each site is edited).
- `Concierge/.env.concierge.example` — **modify (Phase 3).** Un-stale the `FINGPT_API_KEY` comment.
- `Docs/source/api_reference.rst`, `Main/README.md` — **modify (Phase 3).** Update the Authentication notes + endpoint tables.
- `Docs/source/project_structure.rst`, `Docs/source/installation/manual_install.rst` — **modify (Phase 3).** Un-stale the frontend-build description now that the build bakes a key via `DefinePlugin` (see Task 7). *(User-facing hosted docs — coordinate with FlyM1ss before editing.)*

---

## Route accounting (every route in `django_config/urls.py`, verified 2026-07-12)

Every route in the file is dispositioned — no endpoint is left unaccounted for. This table IS the gate-list; Task 6 must decorate exactly the 14 "Phase 3" views and nothing else.

| Route(s) | View(s) | Disposition |
|----------|---------|-------------|
| `v1/models`, `v1/chat/completions` | `openai_views.models_list`, `openai_views.chat_completions` | Already gated (in-body `authenticate_request`) — out of scope, unchanged. |
| `health/` | `views.health` | **Exempt** — deploy/liveness probe (`entrypoint.sh`), must stay unauthenticated. |
| `api/axioms/xbrl/<str:filename>/` | `views.xbrl_filing_download` | **Exempt** (DECISION 2026-07-12) — fetched by a plain `<a download>` click (`Main/frontend/src/modules/helpers.js:321`) that cannot attach a header; protected by rate-limit + opaque server-chosen filename. |
| `api/signals/news/` | `signals_views.news_signals` | **Phase 1** — ATL-facing machine endpoint, no in-repo caller. |
| `debug/memory/` | `views_debug.debug_memory` | Out of scope — only registered inside `if settings.DEBUG:` (prod 404s before the view is reachable) and already guarded by `DEBUG_MEMORY_TOKEN`. Not bearer-gated by design; pinned by `tests/test_debug_route_gating.py`. |
| `input_webtext/`, `api/auto_scrape/`, `get_chat_response/`, `get_chat_response_stream/`, `get_adv_response/`, `get_adv_response_stream/`, `get_source_urls/`, `clear_messages/`, `api/get_preferred_urls/`, `api/sync_preferred_urls/`, `log_question/`, `api/get_available_models/`, `api/axioms/validate/`, `api/axioms/has_claims/` | `views.add_webtext`, `auto_scrape`, `chat_response`, `chat_response_stream`, `adv_response`, `adv_response_stream`, `get_sources`, `clear`, `get_preferred_urls`, `sync_preferred_urls`, `log_question`, `get_available_models`, `validate_claims`, `has_axiom_claims` (14 views) | **Phase 3** — extension-facing; gate after the Phase 2 header build propagates. `get_chat_response_stream/` has a second client (Concierge) → Task 7 env must ship with/before this deploy. |

**Tally:** 14 views get `@require_bearer_auth` (Phase 3) + 1 (`signals/news`) in Phase 1 + 2 exempt (`health`, `xbrl`) + 1 out-of-scope DEBUG-only (`debug/memory`) + 2 already-gated (`/v1`) = every route registered in `django_config/urls.py`.

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

def test_decorator_503_when_required_and_no_key(monkeypatch):
    # Prod fail-closed (spec req 3): REQUIRE_FINGPT_API_KEY=True + no key -> 503, never silent-open.
    monkeypatch.delenv("FINGPT_API_KEY", raising=False)
    with override_settings(REQUIRE_FINGPT_API_KEY=True):
        resp = _probe(RequestFactory().get("/x/"))
    assert resp.status_code == 503
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

## Phase 2 — Extension sends the header (outward-facing; CONFIRM the cutover before merge)

> **KEY-DELIVERY DECISION — RESOLVED 2026-07-12 (build investigation, this session): Option A — bake the coarse-gate key into the bundle at build time via a webpack `DefinePlugin` reading `process.env.FINGPT_API_KEY`.** Rationale + why the alternatives were rejected:
> - The old plan assumed we'd "match how `AGENTIC_BACKEND_URL` is injected." **That premise was false:** `AGENTIC_BACKEND_URL` is *never* injected — `Main/frontend/src/modules/backendConfig.js:4` hardcodes `DEFAULT_BACKEND_BASE_URL = 'https://agenticfinsearch.org'` as a source constant, baked verbatim into `dist/main.js`. There is **no** existing build-injection machinery: no `DefinePlugin`, no `.env`/dotenv, no `EnvironmentPlugin`; the only build plugin that moves data is `CopyPlugin`, which static-copies files with no substitution (`Main/frontend/webpack.config.js:123-129`). So a build-time key requires *adding* a define stage — Task 4 does exactly that.
> - **Rejected — A1 (hardcode the key in `backendConfig.js` source, parallel to the URL constant):** puts a key literal in the git history of a likely-public repo and risks GitHub push-protection / secret-scanning blocking the commit. A build-time `DefinePlugin` keeps the literal in the *build environment*, out of committed source, while still baking it into the (extractable) public bundle — identical coarse-gate threat model, better hygiene and rotation.
> - **Rejected — B (user-pasted key via a settings field + `chrome.storage.local`):** the extension has **no options/settings UI wired to config today** (`Main/frontend/src/manifest.json` declares no `options_page`/`options_ui`/`action`; `settings_window.js` only does model selection). Building that UI is a large lift for **zero** security gain — the key is a coarse gate either way — and imposes per-user paste friction on a public extension. The `chrome.storage` primitive exists (permission declared at `manifest.json:38-40`, used only for layout in `layoutState.js:18`) and is the natural seam for the *future* per-user login key; that is explicitly deferred, not built now.
>
> The only remaining Phase-2/3 sign-off is the **propagation-window cutover** (ship Phase 2, wait for installs to update, then Phase 3) — a scheduling call for FlyM1ss, not a design gap.

### Task 4: Build-time key injection + central auth-header helper

**Files:**
- Modify: `Main/frontend/webpack.config.js` (add `webpack.DefinePlugin` for `process.env.FINGPT_API_KEY`)
- Modify: `Main/frontend/src/modules/backendConfig.js` (add `getAuthHeaders()` + the baked-key constant near `DEFAULT_BACKEND_BASE_URL`)
- Verify: **manual** — `Main/frontend` has **no JS test framework** (webpack-only build; devDeps are babel/css/webpack tooling only). Phase-2 verification is a build + `grep`/DevTools check, recorded in the PR.

**Interfaces:**
- Produces: `getAuthHeaders() -> { Authorization: string } | {}` — returns `{}` when no key is baked, so dev builds send no header and keep working against an open dev backend.

- [ ] **Step 1: Add the `DefinePlugin` stage.** In `Main/frontend/webpack.config.js`, inside the existing `plugins: [...]` array (alongside `EnsureUTF8Plugin`/`BannerPlugin`/`CopyPlugin` — `webpack` is already imported there via `webpack.BannerPlugin`), add:

```js
// Bake the coarse-gate API key from the build env into the bundle. Empty when
// FINGPT_API_KEY is unset (local/dev builds) -> getAuthHeaders() returns {}.
new webpack.DefinePlugin({
  'process.env.FINGPT_API_KEY': JSON.stringify(process.env.FINGPT_API_KEY || ''),
}),
```

- [ ] **Step 2: Add the header helper** to `Main/frontend/src/modules/backendConfig.js` (near the existing `DEFAULT_BACKEND_BASE_URL` constant / exports):

```js
// Coarse-gate bearer key, baked at build time by the webpack DefinePlugin from
// the build environment (process.env.FINGPT_API_KEY). Empty in local/dev builds,
// so no Authorization header is sent and the extension works against an open dev
// backend. A public extension bundle is EXTRACTABLE — this is a COARSE gate
// against drive-by API abuse, NOT per-user auth (deferred to the backend login
// system, api/identity.py). Tests/dev may override via window.FINGPT_API_KEY.
const COARSE_GATE_KEY =
  (typeof window !== 'undefined' && window.FINGPT_API_KEY) ||
  process.env.FINGPT_API_KEY ||
  '';

export function getAuthHeaders() {
  return COARSE_GATE_KEY ? { Authorization: `Bearer ${COARSE_GATE_KEY}` } : {};
}
```

- [ ] **Step 3: Build with a key and confirm it bakes in.** `cd Main/frontend && FINGPT_API_KEY=devkey bun run build:full`, then confirm the literal is present: `grep -c 'devkey' dist/main.js` → `1`. Rebuild with no env var (`bun run build:full`) and confirm `grep -c 'devkey' dist/main.js` → `0` (dev build sends no header). Record both in the PR.

### Task 5: Attach the header at every backend `fetch` call site

**Files:**
- Modify: `Main/frontend/src/modules/api.js` (10 fetch sites), `Main/frontend/src/modules/config.js` (1), `Main/frontend/src/modules/components/link_manager.js` (2) — **13 backend fetch call sites, no central wrapper today** (each inlines `credentials:'include'` + `Content-Type`; `Authorization` appears at zero sites currently).

**Interfaces:**
- Consumes: `getAuthHeaders` from Task 4 (`import { getAuthHeaders } from './backendConfig.js'`; adjust the relative path for `components/link_manager.js`).

- [ ] **Step 1: Enumerate the sites** (line numbers drift as you edit — re-derive, don't trust them): `cd Main/frontend/src/modules && grep -n 'fetch(' api.js config.js components/link_manager.js` → expect **13** hits. As of 2026-07-12: `api.js:19,104,298,375,405,422,450,500,531,549`, `config.js:14`, `link_manager.js:50,179`.

- [ ] **Step 2: Import the helper** at the top of each of the 3 files (matching the existing `backendConfig` import already present for `buildBackendUrl`).

- [ ] **Step 3: Merge `...getAuthHeaders()` into each call's `headers`.** Two shapes occur — POST-with-headers (spread into the existing object):

```js
fetch(buildBackendUrl('/input_webtext/'), {
    method: "POST",
    credentials: "include",
    headers: {
        "Content-Type": "application/json",
        ...getAuthHeaders(),           // <-- added
    },
    // ...body unchanged
```

and GET-without-a-headers-key (add one):

```js
fetch(buildBackendUrl('/api/get_available_models/'), {
    method: "GET",
    credentials: "include",
    headers: { ...getAuthHeaders() },  // <-- added
});
```

Preserve every existing `credentials`, `method`, `Content-Type`, and body verbatim. Adding the header to a not-yet-gated or exempt route is harmless (an unused header), so add it to all 13 for uniformity.

- [ ] **Step 4: Leave the xbrl download untouched.** `Main/frontend/src/modules/helpers.js:321` uses a plain `<a download>` (not a `fetch`), so it is not among the 13 sites and correctly gets no header (route is exempt).

- [ ] **Step 5: Build + verify the header on the wire.** `cd Main/frontend && FINGPT_API_KEY=devkey bun run build:full`. Load the unpacked `dist/` against a local backend started with `FINGPT_API_KEY=devkey`; in DevTools → Network, confirm requests to gated routes carry `Authorization: Bearer devkey` and still succeed. Record in the PR (headless CI cannot load the extension).

- [ ] **Step 6: Commit + PR** — `feat(ext): attach coarse-gate bearer header to backend calls`. **Ship this and let the built extension propagate to installs BEFORE Phase 3 flips the routes to enforced.**

---

## Phase 3 — Gate extension routes + Concierge + docs (execute after Phase 2 propagates)

### Task 6: Gate the 14 extension views

**Files:**
- Modify: `Main/backend/api/views.py` (add import + `@require_bearer_auth` on 14 views)
- Test: `Main/backend/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `require_bearer_auth` from Task 1 (`from api.auth import require_bearer_auth`).

**Placement rule (two decorator shapes — verified against current `main`):**
- **Group A — views that have `@csrf_exempt`** (12): insert `@require_bearer_auth` on the line **directly below `@csrf_exempt`** (above `@require_http_methods`/`@ratelimit`). `@csrf_exempt` must remain the outermost decorator — the URL-resolved callable has to carry the `csrf_exempt=True` marker or CSRF middleware would start rejecting the POST routes. Views (with the `def` line as of 2026-07-12): `has_axiom_claims` (`:178`), `validate_claims` (`:227`), `chat_response` (`:265`), `adv_response` (`:365`), `chat_response_stream` (`:494`), `adv_response_stream` (`:685`), `auto_scrape` (`:888`), `add_webtext` (`:952`), `clear` (`:1011`), `get_sources` (`:1061`), `log_question` (`:1081`), `sync_preferred_urls` (`:1110`).
- **Group B — views with NO `@csrf_exempt`** (2, GET reads): insert `@require_bearer_auth` as the **topmost** decorator (above `@ratelimit`). Views: `get_preferred_urls` (`:1101`), `get_available_models` (`:1129`).
- **Do NOT decorate** `health` (`:1045`, undecorated — exempt) or `xbrl_filing_download` (`:201`, exempt). Confirm both stay untouched in the diff.

- [ ] **Step 1: Add the import + write the failing deny test.** Add near the other `api.*` imports at the top of `views.py`: `from api.auth import require_bearer_auth`. Then add to `tests/test_api_auth.py`:

```python
# tests/test_api_auth.py (add — Phase 3)
import pytest
from django.test import Client, override_settings

# The 14 extension routes that MUST enforce bearer auth (health + xbrl excluded).
GATED_EXTENSION_PATHS = [
    "/input_webtext/", "/api/auto_scrape/", "/get_chat_response/",
    "/get_chat_response_stream/", "/get_adv_response/", "/get_adv_response_stream/",
    "/get_source_urls/", "/clear_messages/", "/api/get_preferred_urls/",
    "/api/sync_preferred_urls/", "/log_question/", "/api/get_available_models/",
    "/api/axioms/validate/", "/api/axioms/has_claims/",
]

@pytest.mark.parametrize("path", GATED_EXTENSION_PATHS)
def test_extension_route_401_without_header(monkeypatch, path):
    # Auth is the outermost gate: no header -> 401 before method-check/ratelimit/view body.
    # A GET short-circuits at auth, so this never runs the heavy chat/scrape view bodies.
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    assert Client().get(path).status_code == 401
```

- [ ] **Step 2: Run to verify it fails.** `cd Main/backend && uv run pytest tests/test_api_auth.py -k extension_route_401 -v` → FAIL: ungated routes run the view and return non-401 (400/404/405/200).

- [ ] **Step 3: Apply the decorators** per the Placement rule above. Group A example (`chat_response_stream`):

```python
@csrf_exempt
@require_bearer_auth          # <-- added, directly under @csrf_exempt
@require_http_methods(['GET', 'POST'])
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT, method=ALL, block=True)
def chat_response_stream(request: HttpRequest) -> StreamingHttpResponse:
```

Group B example (`get_available_models`, no `@csrf_exempt`):

```python
@require_bearer_auth          # <-- added, topmost
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT, method=ALL, block=True)
def get_available_models(request: HttpRequest) -> JsonResponse:
```

- [ ] **Step 4: Run the deny test.** `uv run pytest tests/test_api_auth.py -k extension_route_401 -v` → all 14 PASS.

- [ ] **Step 5: Add allow-side + dev-open + exemption tests**, then run. The allow-side proves a valid key is *accepted* without running a heavy view body: the only two **POST-only** routes (`validate_claims`, `clear`) return **405** via `@require_http_methods` once auth has passed (the body never runs, so no LLM/scrape/session-DB hit), and `get_available_models` is a static read. Chat/stream/scrape/telemetry/prefs-read allow-paths accept GET and would execute their bodies against the no-DB harness, so their allow-side is covered by the Task 8 E2E, not here:

```python
# tests/test_api_auth.py (add)
# Allow-testable at unit level without running a heavy body:
#   - POST-only routes: GET+header passes auth, then 405 (body never runs) -> spans axioms + context.
#   - get_available_models: a static read.
SAFE_ALLOW_PATHS = [
    "/api/axioms/validate/",       # axioms group  (POST-only -> 405)
    "/clear_messages/",            # context group (POST-only -> 405)
    "/api/get_available_models/",  # static read   (-> 200-ish)
]

@pytest.mark.parametrize("path", SAFE_ALLOW_PATHS)
def test_gated_route_accepts_valid_header(monkeypatch, path):
    # A correct key clears the auth gate: the response is anything BUT 401.
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    assert Client().get(path, HTTP_AUTHORIZATION="Bearer sekret").status_code != 401

@override_settings(REQUIRE_FINGPT_API_KEY=False)
def test_gated_route_open_in_dev(monkeypatch):
    monkeypatch.delenv("FINGPT_API_KEY", raising=False)
    assert Client().get("/api/get_available_models/").status_code != 401

def test_exempt_routes_never_401(monkeypatch):
    # health + xbrl download must stay reachable without a header.
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    assert Client().get("/health/").status_code != 401
    assert Client().get("/api/axioms/xbrl/nope.json/").status_code != 401  # 400/404 from view, not 401
```

Run: `uv run pytest tests/test_api_auth.py -v` → all PASS.

- [ ] **Step 6: Full backend suite.** `cd Main/backend && uv run pytest` → all green (no regression to the existing view/ratelimit/signals tests).

- [ ] **Step 7: Commit.**

```
feat(api): gate the 14 extension views with bearer auth (F: endpoint-auth-01 P1)

Apply @require_bearer_auth to every extension-facing non-/v1 view; health +
xbrl download stay exempt (see Route accounting). Ships only after the Phase 2
header build has propagated to installs. Dev-open / prod-fail-closed unchanged.
```

### Task 7: Concierge env + docs

> **DONE 2026-07-13 (loop-dev).** Prod Concierge env keyed = backend `FINGPT_API_KEY` (hash `53f0fb07`, backup `.env.concierge.bak-20260712-174051`) + `concierge.service` restarted clean; `.env.concierge.example` already shipped in the P3 commit; all 4 user-facing docs updated with `sphinx-build -W` clean (commit `c746188`), merged with #356 (squash `cb5dfdf`). All boxes below satisfied.

- [x] `Concierge/.env.concierge.example:11-12` — replace "Usually unset — the extension endpoints aren't Bearer-gated." with a note that `FINGPT_API_KEY` is now REQUIRED to reach the (Bearer-gated) `get_chat_response_stream/`; keep the var line uncommented-with-placeholder.
- [ ] `Docs/source/api_reference.rst:49-53` + `:495-503` — rewrite the "do not currently require an API key"/"unauthenticated" notes to state bearer auth is now required (except `health/` + the `xbrl` download); add an Auth row to the extension endpoint table (`:505-559`) and `-H "Authorization: Bearer $API_KEY"` to the extension cURL examples. Sphinx builds with zero warnings (`cd Main/backend && uv run --group docs sphinx-build -W ...`).
- [ ] `Main/README.md:99-109` + `:350-365` — add auth info to the extension endpoint lists/tables.
- [ ] `Docs/source/project_structure.rst` "Frontend Highlights" (~`:118-174`) — currently describes the build as pure Babel+webpack bundling; note the new `DefinePlugin` build-time key-injection stage so the description isn't stale.
- [ ] `Docs/source/installation/manual_install.rst:35-49` ("Frontend Build (optional)") — currently documents only the backend `.env` keys; add that a release frontend build bakes the coarse-gate key via `FINGPT_API_KEY=... bun run build:full`, and clarify it is distinct from the backend key. (`Docs/frontend_backend_switch.md` needs no change — URL-resolution order is untouched; `Docs/source/installation/chrome_web_store.rst` needs no change — Option A is transparent to end users, no paste step.)
- [ ] **These four `Docs/source/*` + README pages are user-facing hosted docs — coordinate the wording with FlyM1ss before committing; they are listed here so the executor doesn't miss them, not as a mandate to edit unilaterally.**

### Task 8: E2E verification (spec acceptance)

> **REMAINING — user-owned.** FlyM1ss verifies manually. Also (next day): rebuild the published Chrome Web Store extension WITH the coarse-gate key and upload to the CWS listing (`aehnlpneoncdfioafiigiljmbghccami`) before announcing the extension as usable. No live CWS users yet (FlyM1ss confirmed), so the P3 enforcement merge is safe ahead of that republish.

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
- Resolved this session (build investigation): Phase-2 key-delivery = **Option A / webpack `DefinePlugin`** (rationale + why A1 and B were rejected is in Phase 2). Route accounting corrected to **14 gated views** (was mis-stated as 15) with a full per-route disposition table. Fetch-site count corrected to **13** (was 15) with verified line refs.
- Only remaining sign-off (not a design gap): the **Phase-3 propagation-window cutover** — a scheduling call FlyM1ss makes before the Phase-3 merge, because a public extension can't be updated atomically across installs.
