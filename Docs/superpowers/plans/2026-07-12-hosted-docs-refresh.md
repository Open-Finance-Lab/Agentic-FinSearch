# Hosted Docs Refresh (Docs/source/) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the hosted Sphinx docs (`Docs/source/`, published via ReadTheDocs) back in sync with the shipped 0.16.0 codebase, and drop the dead mem0 context path (code + docs) entirely.

**Architecture:** Two stacked PRs. PR-1 (`chore/drop-mem0`) deletes the dead mem0 code path and rewrites the two doc pages that describe it, so the branch is atomically truthful. PR-2 (`docs/hosted-docs-refresh`, stacked on PR-1) carries every other doc correction. A final non-repo task routes the endpoint-auth-tightening item into the security-gate track in the Central DB.

**Tech Stack:** Sphinx (RTD theme, built by ReadTheDocs from `requirements_sphinx.txt`), reStructuredText, uv (backend deps), pytest.

**Decisions locked by FlyM1ss (2026-07-12):**
1. mem0 → **completely dropped** (code, dep, env templates, docs).
2. Missing auth on the 16 non-`/v1` endpoints → **doc scoped to `/v1` now**; actual auth tightening is routed to the security-gate track (Task 13), NOT fixed in this plan.

## Global Constraints

- **Never edit `Docs/source/` outside this plan.** These are user-facing docs; FlyM1ss coordinates their updates explicitly — this plan IS that coordination. No opportunistic extra edits.
- **Sphinx must build with ZERO warnings.** Baseline verified 2026-07-12: the RTD-equivalent build succeeds with no warnings. Build command (verified working):
  ```bash
  SCRATCH=/tmp/claude-1000/-mnt-d-fingpt-github-fingpt-rcos/d1776d09-d81e-4f16-9ac8-61ac66eecb46/scratchpad
  # one-time venv setup (already created at $SCRATCH/docs-venv during planning):
  #   uv venv "$SCRATCH/docs-venv" && uv pip install -p "$SCRATCH/docs-venv" -r requirements_sphinx.txt sphinx
  cd /mnt/d/fingpt/github/fingpt_rcos
  "$SCRATCH/docs-venv/bin/sphinx-build" -b html Docs/source "$SCRATCH/docs-html" 2>&1 | tail -5
  # Expected: "build succeeded." with no warning lines
  ```
  Do NOT use `uv run --group docs sphinx-build` — the backend docs group is currently broken (the `bleach>=6.4.0` security override crashes old `nbconvert`: `NameError: ALLOWED_STYLES`). That breakage is logged as follow-up F-1, out of scope here.
- **Documented version is 0.16.0** (from `Main/backend/pyproject.toml:3`).
- Every doc claim in this plan was verified against code on 2026-07-12; file:line citations are included so the executor can re-verify, not re-research.
- Repo convention: conventional-commit messages; work lands via PR (do not push to `main`).
- No screenshots need re-capture (verified: no image refs in the affected pages; the one XBRL pipeline figure remains accurate).

---

### Task 1: Drop the dead mem0 code path (PR-1, code side)

**Files:**
- Delete: `Main/backend/datascraper/mem0_context_manager.py`
- Delete: `Main/backend/datascraper/context_integration_enhanced.py`
- Modify: `Main/backend/pyproject.toml:16` (dep) and `:67` (comment)
- Modify: `Main/backend/.env.example:16,44-54`
- Modify: `Main/backend/.env.production.example:92-103`
- Regenerate: `Main/backend/uv.lock`

**Interfaces:**
- Consumes: nothing (both modules have ZERO importers — verified: the only references to `EnhancedContextIntegration`/`Mem0ContextManager`/`context_integration_enhanced`/`mem0_context_manager` are inside the two files themselves; the live path `datascraper/context_integration.py:12-16` imports only `unified_context_manager`).
- Produces: a backend with no mem0 code, no `mem0ai` dependency, no `CONTEXT_MANAGER_MODE`/`MEM0_*` env surface. Later tasks rely on `grep -rni mem0` matching only the two historical security comments listed in Step 7.

- [ ] **Step 1: Branch**

```bash
cd /mnt/d/fingpt/github/fingpt_rcos
git checkout main && git pull && git checkout -b chore/drop-mem0
```

- [ ] **Step 2: Re-verify zero importers (fail the task if this ever prints a line)**

```bash
cd Main/backend
grep -rn "context_integration_enhanced\|mem0_context_manager\|EnhancedContextIntegration\|Mem0ContextManager" \
  --include="*.py" --exclude-dir=.venv --exclude-dir=__pycache__ . \
  | grep -v "^./datascraper/context_integration_enhanced.py\|^./datascraper/mem0_context_manager.py"
```
Expected: **no output**.

- [ ] **Step 3: Delete the two modules**

```bash
git rm datascraper/mem0_context_manager.py datascraper/context_integration_enhanced.py
```

- [ ] **Step 4: Edit `pyproject.toml`**

Remove this line from `dependencies` (line 16):
```toml
    "mem0ai>=1.0.0,<2",
```
And change the protobuf override comment (line 67) — the floor STAYS (it is a defensive CVE floor, other deps may still resolve protobuf):
```toml
# old
    "protobuf>=6.33.5",        # CVE-2026-0994: JSON recursion depth bypass (overrides mem0ai's <6 pin)
# new
    "protobuf>=6.33.5",        # CVE-2026-0994: JSON recursion depth bypass
```

- [ ] **Step 5: Edit `.env.example`**

Remove line 16:
```bash
MEM0_API_KEY=your-mem0-api-key-here
```
Add in its place (same API Keys block; the default `FinGPT` model is Gemini via the `google` provider — `datascraper/models_config.py:58-62` reads `GOOGLE_API_KEY`, which this template never listed):
```bash
GOOGLE_API_KEY=your-google-api-key-here
```
Remove lines 44-54 entirely (the whole block):
```bash
# Context Manager Configuration
# Choose between 'unified' (no compression) or 'mem0' (smart compression with 100k token limit)
CONTEXT_MANAGER_MODE=mem0

# Mem0 Context Manager Settings (only used when CONTEXT_MANAGER_MODE=mem0)
# Maximum tokens before triggering smart compression (default: 100000)
MEM0_CONTEXT_TOKEN_LIMIT=100000
# Target compression ratio when limit is reached (0.4 to 0.9, default: 0.7)
MEM0_COMPRESSION_TARGET_RATIO=0.7
# Maximum characters for compressed chunks (default: 4000)
MEM0_COMPRESSION_MAX_CHARS=4000
```

- [ ] **Step 6: Edit `.env.production.example`** — remove lines 92-103 (same shape of block: `# Context Manager Configuration` header through `MEM0_COMPRESSION_MAX_CHARS=4000`, including the `# For production, 'mem0' is recommended for better scalability` line).

- [ ] **Step 7: Explicitly LEAVE these two references alone** (do not "clean them up"):
  - `mcp_client/mcp_manager.py:143` — comment "…OPENAI_API_KEY / DJANGO_SECRET_KEY / Redis + Mem0 credentials…": historical narrative of the 2026-06-29 Root-G audit fix; describes what `os.environ` carried *then*.
  - `tests/test_mcp_child_env.py:5,52` — same historical docstring, plus the `"MEM0_API_KEY": "poison-mem0-key"` poison entry, which stays valid as a generic third-party-secret probe (the test asserts an allow-list, not a denylist).

- [ ] **Step 8: Relock and test**

```bash
uv lock
grep -c 'name = "mem0ai"' uv.lock   # Expected: 0
uv sync
uv run pytest -q
```
Expected: lock succeeds, grep prints `0`, full suite passes (no test imports the deleted modules — verified Step 2).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "chore(context): drop dead mem0 context path (code, deps, env templates)

The mem0/CONTEXT_MANAGER_MODE dual-mode was dead code: the live request path
(datascraper/context_integration.py) has been hardcoded to
UnifiedContextManager, and context_integration_enhanced.py /
mem0_context_manager.py had zero importers. Removes both modules, the mem0ai
dependency, and the MEM0_*/CONTEXT_MANAGER_MODE env template surface. Adds
the previously-missing GOOGLE_API_KEY to .env.example (default FinGPT model
is Gemini). protobuf CVE floor retained."
```

*(Local note, not a repo change: `Main/backend/.env` is gitignored and still carries `CONTEXT_MANAGER_MODE=unified` + the Mem0 comment on lines 45-47 — harmless, nothing reads it anymore; delete at leisure.)*

---

### Task 2: Rewrite the context-system docs (PR-1, docs side)

**Files:**
- Rewrite: `Docs/source/usage/memory_system.rst` (whole file)
- Modify: `Docs/source/usage/advanced_usage.rst:160-178`
- Modify: `Docs/source/project_structure.rst:50` (one tree line)

**Interfaces:**
- Consumes: post-Task-1 reality (single context system).
- Produces: `grep -rni mem0 Docs/source` returns nothing; Task 7 edits `advanced_usage.rst` again later, so the section header written here (`Context Management`) must not be renamed by Task 7.

**Ground truth for the new content** (verified 2026-07-12): `UnifiedContextManager` stores session state in the Django cache — `FileBasedCache` at `/tmp/fingpt_cache` in dev (`settings.py:80-85`), `RedisCache` in prod (`settings_prod.py:87-92`), both `TIMEOUT: 3600`. Fetched web content capped at 10,000 chars/item (`context_integration.py:118-120`). Modes NORMAL/THINKING/RESEARCH selected per endpoint (`context_integration.py:40-54`). Conversation keys are rooted in the signed `fingpt_sessionid` cookie with caller `session_id` namespaced under it (`datascraper/session_key.py:1-18`); the extension stores a per-tab id in `sessionStorage` (`frontend/src/modules/api.js:7-12`), so tab isolation holds as a sub-namespace.

- [ ] **Step 1: Replace the full contents of `usage/memory_system.rst` with:**

```rst
Memory and Context System
=========================

Agentic FinSearch tracks every conversation with a single, session-scoped
context system: the ``UnifiedContextManager`` in
``Main/backend/datascraper/unified_context_manager.py``.

How It Works
------------

- **Session-Based**: Each browser tab (and each API caller) gets its own
  isolated context. See `Session Isolation`_ below.
- **Full History**: The complete conversation history for the current session
  — user messages, assistant responses, and their metadata (model used,
  sources, tool calls) — is retained and replayed to the model on every turn.
- **Fetched Context**: Scraped page content and search results are stored
  alongside the conversation (each item capped at 10,000 characters) and
  injected into the model's context for the session.
- **Modes**: Each request runs in one of three context modes — ``normal``,
  ``thinking``, or ``research`` — chosen by the endpoint handling the request
  (or an explicit ``mode`` parameter).
- **Storage & Expiry**: Session state lives in the Django cache — a local
  file cache in development, Redis in production — and expires after **1
  hour** of inactivity.

Session Isolation
-----------------

- **Cookie-Rooted Keys**: Conversation keys are rooted in a per-browser ID
  stored inside the **signed session cookie** (``fingpt_sessionid``), so a
  caller can never read or poison another caller's history by guessing a
  session ID.
- **Tab Isolation**: The extension keeps a per-tab session ID (browser
  ``sessionStorage``) that is namespaced *under* the cookie root — each tab
  gets its own conversation, and it still cannot cross to another browser.
- **API Isolation**: OpenAI-compatible API requests with a ``user`` parameter
  get a per-user session; requests without one get a unique ephemeral
  session.
- **Manual Clearing**: Use the **Clear** button to reset the current session's
  conversation history while optionally preserving scraped web content.
```

- [ ] **Step 2: In `usage/advanced_usage.rst`, replace lines 160-178** (the `Smart Context Management` section from its `~~~` header through the `- **Session Isolation**: Each browser tab/session maintains its own isolated context.` bullet — keep the `.. note::` that follows) **with:**

```rst
Context Management
~~~~~~~~~~~~~~~~~~

Agentic FinSearch tracks each session with the **Unified Context Manager** —
session-scoped conversation history, scraped page content, and research
findings held in a structured JSON form. See :doc:`memory_system` for
details.

**How it works:**

- **Session-Based**: The agent maintains the full conversation history for the current session.
- **Storage & Expiry**: Session state lives in the Django cache (file-based in development, Redis in production) and expires after **1 hour** of inactivity.
- **Session Isolation**: Each browser tab/session maintains its own isolated context.
```

- [ ] **Step 3: In `project_structure.rst`, delete line 50:**

```text
   │   ├── mem0_context_manager.py   # Memory-based context (Mem0)
```

- [ ] **Step 4: Build + verify**

```bash
cd /mnt/d/fingpt/github/fingpt_rcos
"$SCRATCH/docs-venv/bin/sphinx-build" -b html Docs/source "$SCRATCH/docs-html" 2>&1 | tail -3
grep -rni "mem0\|CONTEXT_MANAGER_MODE" Docs/source/
```
Expected: `build succeeded.`, zero warnings, grep **empty**.

- [ ] **Step 5: Commit, push, open PR-1**

```bash
git add Docs/source
git commit -m "docs(context): rewrite memory docs for the unified context manager"
git push -u origin chore/drop-mem0
gh pr create --title "chore: drop dead mem0 context path (code + docs)" --body "The mem0/CONTEXT_MANAGER_MODE dual-mode was dead code: the live request path (datascraper/context_integration.py) is hardcoded to UnifiedContextManager, and context_integration_enhanced.py / mem0_context_manager.py had zero importers.

- Delete both dead modules; drop the mem0ai dependency (uv.lock regenerated); keep the protobuf CVE floor.
- Remove MEM0_*/CONTEXT_MANAGER_MODE from both .env templates; add the previously-missing GOOGLE_API_KEY (default FinGPT model is Gemini).
- Rewrite usage/memory_system.rst + the advanced_usage context section to describe the unified context manager (Django cache storage, 1h TTL, cookie-rooted session isolation).
- Historical Mem0 mentions in the 2026-06-29 Root-G security comments are intentionally left as-is.

Decided by FlyM1ss 2026-07-12 (hosted-docs refresh, plan: Docs/superpowers/plans/2026-07-12-hosted-docs-refresh.md)."
```

---

### Task 3: Start PR-2 branch + version bump

**Files:**
- Modify: `Docs/source/conf.py:12`
- Modify: `Docs/source/api_reference.rst:107`

- [ ] **Step 1: Branch (stacked on PR-1 so docs describe post-drop reality)**

```bash
git checkout -b docs/hosted-docs-refresh chore/drop-mem0
```

- [ ] **Step 2: `conf.py:12`** — `release = '0.13.3'` → `release = '0.16.0'`

- [ ] **Step 3: `api_reference.rst:107`** — in the health-check example response, `"version": "0.13.3",` → `"version": "0.16.0",`

- [ ] **Step 4: Build (zero warnings), verify, commit:**

```bash
grep -rn "0.13.3" Docs/source/conf.py Docs/source/api_reference.rst   # expect empty
# NOTE: index.rst + updates.rst legitimately keep "0.13.x" as release HISTORY — do not touch them.
git commit -am "docs: bump documented version 0.13.3 -> 0.16.0"
```

---

### Task 4: api_reference — scope the auth claims to /v1

**Files:**
- Modify: `Docs/source/api_reference.rst:4,34-45`

**Ground truth:** only `openai_views.py` calls `_authenticate_request` (lines 161, 211 — i.e. `/v1/models` + `/v1/chat/completions`). Missing-key behavior: dev mode accepts all; with `REQUIRE_FINGPT_API_KEY=True` (set unconditionally in `settings_prod.py:71`) a missing key **fails closed with 503** (`openai_views.py:93-103`). The other 17 routes have no auth — rate limiting + cookie-rooted sessions only.

- [ ] **Step 1: Replace line 4:**

```rst
This document specifies the Agentic FinSearch OpenAI-compatible REST API. The API is **synchronous** (no streaming). All request and response bodies are JSON.
```
with:
```rst
This document specifies the Agentic FinSearch REST API: the OpenAI-compatible ``/v1`` endpoints plus the extension, XBRL-validation, and news-signals endpoints. The ``/v1`` API is **synchronous** (no streaming); the extension chat endpoints also offer Server-Sent-Events streaming variants. Unless noted otherwise, request and response bodies are JSON.
```

- [ ] **Step 2: In the `Authentication` subsection, replace this block (lines 37-45):**

```rst
The API uses **Bearer token** authentication.

.. code-block:: text

   Authorization: Bearer <FINGPT_API_KEY>

- The API key is set via the ``FINGPT_API_KEY`` environment variable on the server.
- If ``FINGPT_API_KEY`` is **not set**, authentication is disabled (development mode) and all requests are accepted.
- When authentication is enabled, every request to every endpoint must include the ``Authorization`` header.
```
with:
```rst
The **OpenAI-compatible endpoints** (``/v1/models``, ``/v1/chat/completions``) use **Bearer token** authentication.

.. code-block:: text

   Authorization: Bearer <FINGPT_API_KEY>

- The API key is set via the ``FINGPT_API_KEY`` environment variable on the server.
- If ``FINGPT_API_KEY`` is **not set**, ``/v1/*`` authentication is disabled (development mode). In production the server sets ``REQUIRE_FINGPT_API_KEY=True``, which **fails closed**: a missing key makes ``/v1/*`` return ``503`` instead of silently accepting unauthenticated requests.
- When authentication is enabled, every ``/v1/*`` request must include the ``Authorization`` header.

.. note::
   The extension and utility endpoints documented below do **not** currently
   require an API key. They are protected by per-client rate limiting and
   cookie-rooted session isolation. Extending bearer authentication to these
   endpoints is tracked on the security roadmap.
```
(Keep the two existing 401 example blocks that follow.)

- [ ] **Step 3: Build (zero warnings), commit:**

```bash
git commit -am "docs(api): scope bearer-auth claims to /v1; document fail-closed prod behavior"
```

---

### Task 5: api_reference — document the 16 undocumented endpoints

**Files:**
- Modify: `Docs/source/api_reference.rst` (insert new section between "Available Models" and "Usage Examples"; amend the "Statelessness" behavioral note)

**Ground truth:** endpoint contracts extracted from `api/views.py`, `api/signals_views.py`, `api/openai_views.py` on 2026-07-12 and spot-verified against consumers: `validate` summary shape confirmed against `axioms/__init__.py:100-101` **and** its frontend consumer `helpers.js:718-720` (`{"total": n, "VERIFIED": n, "FAILED": n, "SKIPPED": n, "NOT_APPLICABLE": n, "ERROR": n}`); signals body confirmed against `signals_views.py:158-176` (artifact minus `generator`/`model`/`prompt_version`, plus computed `staleness_hours`; `Cache-Control`/`ETag`/`Last-Modified` are **headers**) and `Heartbeat/schemas/signals-v1.schema.json`.

- [ ] **Step 1: Insert the following as a new top-level section**, immediately after the line `All models support both ``thinking`` (MCP) and ``research`` (deep search) modes.` and its `---` separator (i.e. before `Usage Examples`):

```rst
Extension & Utility Endpoints
-----------------------------

These endpoints back the Chrome extension and other first-party surfaces.
They are **unauthenticated** (see the note under `Authentication`_): each
client is rate-limited, and conversations are isolated via the signed
``fingpt_sessionid`` cookie. Callers may pass an optional ``session_id``
(query string or JSON body) to select a sub-conversation *under their own*
cookie root — it can never address another browser's history.

.. list-table::
   :widths: 12 34 54
   :header-rows: 1

   * - Method
     - Path
     - Purpose
   * - GET/POST
     - ``/get_chat_response/``
     - Thinking-mode answer (synchronous)
   * - GET/POST
     - ``/get_chat_response_stream/``
     - Thinking-mode answer (SSE stream)
   * - GET/POST
     - ``/get_adv_response/``
     - Research-mode answer (synchronous)
   * - GET/POST
     - ``/get_adv_response_stream/``
     - Research-mode answer (SSE stream)
   * - POST
     - ``/input_webtext/``
     - Add scraped page text to the session context
   * - POST
     - ``/api/auto_scrape/``
     - Server-side scrape of the active page (SSRF-guarded)
   * - GET
     - ``/get_source_urls/``
     - Sources for a query
   * - POST
     - ``/clear_messages/``
     - Clear the session conversation
   * - GET
     - ``/api/get_preferred_urls/``
     - Read stored Preferred links
   * - POST
     - ``/api/sync_preferred_urls/``
     - Store Preferred links
   * - GET
     - ``/api/get_available_models/``
     - Model metadata for the Settings dropdown
   * - GET/POST
     - ``/log_question/``
     - Telemetry logging
   * - POST
     - ``/api/axioms/validate/``
     - Run XBRL validation over a session's recorded claims
   * - GET
     - ``/api/axioms/has_claims/``
     - Does this session have validatable claims?
   * - GET
     - ``/api/axioms/xbrl/<filename>/``
     - Serve a bundled XBRL filing (Sources popup)
   * - GET
     - ``/api/signals/news/``
     - Latest news→sentiment signals artifact

All share the ``API_RATE_LIMIT`` budget (``429 {"error": "rate_limited"}``
when exceeded). The chat endpoints can also return ``503 {"error": "busy"}``
when the agent concurrency or daily budget cap is hit.

Chat (Thinking / Research)
~~~~~~~~~~~~~~~~~~~~~~~~~~

``/get_chat_response/`` (Thinking mode) and ``/get_adv_response/`` (Research
mode) accept the same core parameters — query string on GET, JSON body on
POST (the body wins when both are present):

.. list-table::
   :widths: 25 10 65
   :header-rows: 1

   * - Field
     - Required
     - Description
   * - ``question``
     - Yes
     - The user's prompt.
   * - ``models``
     - No
     - Comma-separated model IDs (the extension always sends the model
       chosen in Settings).
   * - ``current_url``
     - No
     - Active page URL, used for context and site-specific prompts.
   * - ``preferred_links``
     - No
     - Research mode only: JSON-encoded array of URLs to prioritize.
   * - ``session_id``
     - No
     - Sub-conversation selector (namespaced under the session cookie).
   * - ``user_timezone`` / ``user_time``
     - No
     - IANA timezone / ISO 8601 timestamp for time-aware answers.

**Response (200):** ``resp`` maps each requested model ID to its response
text. Thinking mode adds ``has_axiom_claims`` (drives the Validate button);
Research mode adds ``used_sources`` (objects with ``url``/``title``/
``snippet``) and ``used_urls``. Both include ``context_stats`` (session id,
mode, message and token counts).

**Streaming variants** (``…_stream/``) return ``text/event-stream``: a
``connected`` event, ``{"status": {…}}`` progress frames,
``{"content": "…", "done": false}`` chunks, and a final ``{"done": true, …}``
frame carrying ``wrapped_content``, ``used_sources``, ``used_urls``, and
``context_stats``. Errors mid-stream arrive as
``{"error": "…", "done": true}``.

Context & Preferences
~~~~~~~~~~~~~~~~~~~~~

``POST /input_webtext/`` — body ``{"textContent": "…", "currentUrl": "…"}``
(``textContent`` required). Appends scraped page text to the session
context. Returns ``{"status": "success", "session_id": …,
"context_stats": {…}}``; ``400`` when ``textContent`` is missing.

``POST /api/auto_scrape/`` — body ``{"current_url": "…"}``. Server-side
scrape of the active page, skipped when already scraped
(``{"status": "skipped", "reason": "already_scraped"}``). Target URLs are
checked against the SSRF egress policy first: blocked targets return
``400 {"error": "URL refused by security policy"}``.

``POST /clear_messages/`` — query parameter ``preserve_web``
(``"true"``/``"false"``, default ``"false"``). Clears the session
conversation, optionally keeping scraped web content.

``GET /get_source_urls/`` — query parameters ``query``, ``current_url``.
Returns ``{"resp": [{"url", "title", "snippet"}, …]}``.

``GET /api/get_preferred_urls/`` → ``{"urls": […]}``.
``POST /api/sync_preferred_urls/`` with ``{"urls": […]}`` →
``{"status": "success", "synced": <count>}``.

``GET /api/get_available_models/`` → ``{"models": [{"id", "provider",
"description", "supports_mcp", "supports_advanced", "display_name"}, …]}``.

``GET|POST /log_question/`` — fire-and-forget telemetry (``question``,
``button``, ``current_url``); always returns ``{"status": "success"}``.

XBRL Validation Endpoints
~~~~~~~~~~~~~~~~~~~~~~~~~

These back the per-response **Validate** button (see :doc:`xbrl_validation`).

``GET /api/axioms/has_claims/`` — keyed off the session cookie. Returns
``{"session_id": …, "has_claims": bool, "count": n}``.

``POST /api/axioms/validate/`` — body ``{"session_id": "…"}`` (falls back to
the cookie-derived session). Runs the deterministic Layer-1 proof over every
claim recorded for the session:

.. code-block:: json

   {
     "session_id": "…",
     "claims": [
       {
         "ratio": "gross_margin",
         "ticker": "AAPL",
         "period": "2023-09-30",
         "claimed_value": 0.441,
         "status": "VERIFIED",
         "expected": 0.4413,
         "actual": 0.441,
         "variance_pct": 0.07,
         "formula": "(Revenue - COGS) / Revenue",
         "xbrl_source": "…",
         "message": "…"
       }
     ],
     "summary": {"total": 1, "VERIFIED": 1, "FAILED": 0, "SKIPPED": 0,
                 "NOT_APPLICABLE": 0, "ERROR": 0}
   }

Per-claim ``status`` is one of ``VERIFIED``, ``FAILED``, ``SKIPPED``,
``NOT_APPLICABLE``, ``ERROR``.

``GET /api/axioms/xbrl/<filename>/`` — serves a bundled SEC XBRL filing as
``application/xml`` for the Sources popup. ``filename`` must match
``<ticker>-<yyyymmdd>.xml`` exactly; anything else (including path-traversal
attempts) returns ``404``.

News Signals
~~~~~~~~~~~~

``GET /api/signals/news/`` serves the latest **news→sentiment signals
artifact** produced by the Heartbeat pipeline (``Heartbeat/``). This is an
integration surface for external consumers (e.g., trading-research stacks);
the browser extension does not call it.

**Query parameters:**

- ``as_of=YYYY-MM-DD`` — point-in-time read: returns the newest artifact
  dated on or before that day. Malformed values return
  ``400 {"error": "bad_as_of"}``.
- ``tickers=AAPL,MSFT`` — filter the ``signals`` map to those symbols.

**Response (200):** the artifact JSON (schema:
``Heartbeat/schemas/signals-v1.schema.json``) minus internal provenance
fields, plus a computed ``staleness_hours``. Key fields: ``schema_version``,
``profile``, ``generated_at``, ``window_hours``, ``watchlist``, ``status``
(``ok`` | ``degraded``), ``status_reason``, ``news_overview``,
``diagnostics``, and ``signals`` — a map of ticker →
``{sentiment, score, rationale, headline, source, url, published, guid,
n_articles}`` with ``score`` in ``[-1, 1]``.

``404 {"error": "no_signals"}`` when no artifact exists yet. Responses carry
``ETag`` / ``Last-Modified`` validators and
``Cache-Control: public, max-age=300``.

---
```

- [ ] **Step 2: Amend the Statelessness note** (Behavioral Notes section) — replace:

```rst
The API is **fully stateless**. Each request creates a fresh session context. To maintain conversation history, the client must send the full ``messages`` array with every request.
```
with:
```rst
The **/v1 API is fully stateless**: each request creates a fresh session context, so the client must send the full ``messages`` array every time. The extension endpoints are the opposite — they are session-scoped via the signed ``fingpt_sessionid`` cookie and keep conversation history server-side until the session expires (1 hour idle) or is cleared.
```

- [ ] **Step 3: Build (zero warnings), spot-check the rendered section, commit:**

```bash
git commit -am "docs(api): document extension, axioms, and news-signals endpoints"
```

---

### Task 6: mcp_tools.rst — refresh the server/tool inventory

**Files:**
- Modify: `Docs/source/mcp_tools.rst:9-30` (server list), `:37-48` (architecture), `:53` (enable wording), `:92-99` (examples)

**Ground truth (verified 2026-07-12):** `mcp_server_config.json` — enabled servers: `yahoo-finance`, `tradingview`, `xbrl-taxonomy`, `sec-edgar`; `filesystem` is `disabled` AND all 14 of its tools sit on `DENY_ALWAYS` in `mcp_client/tool_policy.py` (deny-by-default policy, enforced at attach time in `agent.create_fin_agent()` and again at execution in `MCPClientManager.execute_tool()`). Tool counts: Yahoo = 9, TradingView = 7 (crypto-only), XBRL = 3, SEC-EDGAR = 21 (external `sec-edgar-mcp` package). Full names below were copied from the tool definitions.

- [ ] **Step 1: Replace the four server entries (lines 9-30) with:**

```rst
1. **SEC-EDGAR Server**
   - **Purpose**: Access official SEC filings (10-K, 10-Q, 8-K) and XBRL company facts.
   - **Tools** (21, from the external ``sec-edgar-mcp`` package): company lookup (``get_cik_by_ticker``, ``get_company_info``, ``search_companies``, ``get_company_facts``), filings (``get_recent_filings``, ``get_filing_content``, ``get_filing_sections``, ``analyze_8k``), financials (``get_financials``, ``get_segment_data``, ``get_key_metrics``, ``compare_periods``, ``discover_company_metrics``, ``get_xbrl_concepts``, ``discover_xbrl_concepts``), insider activity (``get_insider_transactions``, ``get_insider_summary``, ``get_form4_details``, ``analyze_form4_transactions``, ``analyze_insider_sentiment``), and ``get_recommended_tools``.
   - **Automatic Activation**: Triggered when you ask questions about company filings or historical data.
   - **Transport**: Stdio (``python -m sec_edgar_mcp.server``)

2. **Yahoo Finance Server**
   - **Purpose**: Real-time market data and historical price analysis.
   - **Tools** (9): ``get_stock_info``, ``get_stock_financials``, ``get_stock_news``, ``get_stock_history``, ``get_stock_analysis``, ``get_earnings_info``, ``get_options_chain``, ``get_options_summary``, ``get_holders``.
   - **Automatic Activation**: Used for stock price queries and basic market research.
   - **Transport**: Stdio (custom server in ``mcp_server/yahoo_finance_server.py``)

3. **TradingView Server**
   - **Purpose**: Technical analysis and screeners for **cryptocurrencies** (crypto exchanges only).
   - **Tools** (7): ``get_coin_analysis``, ``get_top_gainers``, ``get_top_losers``, ``get_bollinger_scan``, ``get_rating_filter``, ``get_consecutive_candles``, ``get_advanced_candle_pattern``.
   - **Automatic Activation**: Used for crypto technical-analysis questions and market screening.
   - **Transport**: Stdio (custom server in ``mcp_server/tradingview/``)

4. **XBRL Taxonomy Server**
   - **Purpose**: Ground XBRL tagging in the official US-GAAP 2026 taxonomy.
   - **Tools** (3): ``lookup_xbrl_tags`` (natural-language taxonomy search), ``validate_xbrl_tag`` (does this tag exist?), ``query_xbrl_filing`` (reported values for a tag in a bundled filing).
   - **Automatic Activation**: Backs Stage 1 of the :doc:`XBRL validation pipeline <xbrl_validation>` and taxonomy questions.
   - **Transport**: Stdio (custom server in ``mcp_server/xbrl/``)

.. note::
   A generic **Filesystem server** (``@modelcontextprotocol/server-filesystem``)
   exists in the configuration but is **disabled**, and all 14 of its tools sit
   on a permanent deny-list in ``mcp_client/tool_policy.py`` — they are never
   reachable regardless of configuration.
```

- [ ] **Step 2: In the Architecture section, extend the MCP Client list (after the ``tool_wrapper.py`` bullet, line 41) with:**

```rst
- ``tool_policy.py``: Deny-by-default tool policy — tools reach the agent only through explicit allow-lists, enforced when tools are attached **and** again at execution time; the filesystem server's tools are permanently denied.
```

And extend the MCP Servers list (after the ``tradingview/`` bullet, line 46) with:

```rst
- ``xbrl/``: XBRL taxonomy server (US-GAAP 2026 taxonomy search and validation) plus bundled sample filings.
```

- [ ] **Step 3: Replace the "How to Enable" opening line (line 53):**

```rst
MCP tools are enabled by default. The agent automatically connects to all servers defined in ``mcp_server_config.json`` on startup.
```
with:
```rst
MCP tools are enabled by default. On startup the agent connects to every server marked ``"enabled": true`` in ``mcp_server_config.json``; a tool must additionally be on the active allow-list (``mcp_client/tool_policy.py``) to reach the agent.
```

- [ ] **Step 4: Fix the TradingView usage example (line 96):**

```rst
- *"What are the technical indicators for TSLA?"* → TradingView
```
with:
```rst
- *"What are the technical indicators for BTC-USD?"* → TradingView (crypto)
```

- [ ] **Step 5: Build (zero warnings), verify, commit:**

```bash
grep -n "Filesystem" Docs/source/mcp_tools.rst   # expect ONLY the disabled-server note
git commit -am "docs(mcp): refresh MCP server/tool inventory; mark filesystem server disabled"
```

---

### Task 7: usage docs — replace the Ask/Advanced-Ask UI with reality

**Files:**
- Modify: `Docs/source/usage/basic_usage.rst:28-29,38-52`
- Modify: `Docs/source/usage/advanced_usage.rst:57-60,75,93,104-116,142,186-190`

**Ground truth:** the prompt submits on **Enter** (`chat.js:15-17` → `submit_question(currentMode)`); mode is chosen via a **dropdown** with exactly two options, `Thinking` (default) and `Research` (`chat.js:25-102`). There are no Ask/Advanced-Ask buttons (the old handlers are dead code never wired to the DOM). The pin control is a toggle labeled **"Hover in Place" / "Move with Page"** (`ui.js:41,94`). The per-response **Validate** button appears only when the backend reports ratio claims (`handlers.js:335-344`). All three models support MCP; providers differ (`models_config.py`: FinGPT→Google, FinGPT-Light→OpenAI, Buffet-Agent→HF endpoint).

- [ ] **Step 1: `basic_usage.rst` — replace the pin bullet (lines 28-29):**

```rst
- **Pin-to-Place Button**: Pins the pop up to its current location. The pop up will not move when the user scrolls the
  page.
```
with:
```rst
- **Position-Mode Button**: Toggles how the pop up behaves when you scroll.
  **Hover in Place** keeps the pop up fixed on screen while the page scrolls;
  **Move with Page** lets it scroll with the page content. The button label
  shows the currently active mode.
```

- [ ] **Step 2: `basic_usage.rst` — replace the Prompt Box section (lines 38-52, from the `Prompt Box` header through the `- **Source Button**: …may be closed.'` bullet) with:**

```rst
Prompt Box
~~~~~~~~~~

Type your prompt inside the prompt box and press **Enter** to send it. A
**mode selector** next to the prompt box chooses how the agent answers:

- **Thinking** (default): the agent works from the context scraped from the
  current page and calls MCP tools (SEC-EDGAR, Yahoo Finance, TradingView,
  XBRL taxonomy) when it needs live financial data.

- **Research**: the agent runs the deep-research pipeline — it decomposes the
  question, searches the open web plus your Preferred links in parallel, and
  synthesizes a sourced answer.

More buttons appear above the prompt box and below where conversations are shown.

- **Clear Button**: Clears the currently shown conversations.

- **Source Button**: Shows the sources used by the search agent to answer the
  user's prompt. The sources are shown in a pop up and may be closed.

- **Validate Button**: Appears in a response's toolbar when the response
  contains numerical claims the XBRL pipeline can check. Click it to verify
  each claim against SEC XBRL filings — see :doc:`../xbrl_validation`.
```

- [ ] **Step 3: `advanced_usage.rst` — in `MCP Features` (lines 57-60), replace the Filesystem bullet:**

```rst
- **Filesystem MCP**: Provides read access to local data files within the application directory.
```
with:
```rst
- **XBRL Taxonomy MCP**: Retrieval-then-select over the FASB US-GAAP taxonomy (``lookup_xbrl_tags``, ``validate_xbrl_tag``, ``query_xbrl_filing``) — backs Stage 1 of the :doc:`XBRL validation pipeline <../xbrl_validation>`.
```

- [ ] **Step 4: `advanced_usage.rst:75` — replace:**

```rst
To use deep research mode, click the **Advanced Ask** button or select "research" mode via the API.
```
with:
```rst
To use deep research mode, select **Research** in the mode dropdown next to the prompt box, or pass ``"mode": "research"`` via the API.
```

- [ ] **Step 5: `advanced_usage.rst:93` — replace:**

```rst
The agent will prioritize these sources when using **Advanced Ask**.
```
with:
```rst
The agent will prioritize these sources when answering in **Research** mode.
```

- [ ] **Step 6: `advanced_usage.rst` — replace the Query Modes lists (lines 107-116):**

```rst
**Basic Ask:**
- Searches only the current webpage
- Faster responses
- Best for page-specific questions

**Advanced Ask:**
- Searches the open domain and uses MCP tools
- Activates the deep research pipeline for complex queries
- More comprehensive responses
- Best for research and analysis
```
with:
```rst
**Thinking mode (default):**
- Works from the current page's scraped context and calls MCP tools for live financial data
- Faster responses
- Best for page-specific and targeted financial questions

**Research mode:**
- Runs the deep-research pipeline across the open domain plus your Preferred links
- More comprehensive, multi-source responses
- Best for broad research and analysis
```

- [ ] **Step 7: `advanced_usage.rst:142` — replace `- Citations if using advanced ask` with `- Citations if using Research mode`** (keep list indentation).

- [ ] **Step 8: `advanced_usage.rst` — in Troubleshooting (lines 186-190), replace:**

```rst
- Confirm OpenAI API key is valid
- Check you're using an MCP-compatible model
```
with:
```rst
- Confirm the API key for your selected model's provider is valid: ``FinGPT``
  → ``GOOGLE_API_KEY``, ``FinGPT-Light`` → ``OPENAI_API_KEY``,
  ``Buffet-Agent`` → ``BUFFET_AGENT_API_KEY``.
```

- [ ] **Step 9: Build (zero warnings), then verify no stale UI references remain:**

```bash
grep -rn "Advanced Ask\|Ask Button\|Pin-to-Place" Docs/source/usage/
```
Expected: **no output**.

- [ ] **Step 10: Commit:**

```bash
git commit -am "docs(usage): replace Ask/Advanced-Ask UI with mode dropdown; fix control labels"
```

*(Deliberately NOT changed: the "does NOT work on Brave" note at `basic_usage.rst:8-9` and `advanced_usage.rst:8-9` — unverifiable from code; flagged for manual retest, follow-up F-2.)*

---

### Task 8: xbrl_validation.rst — truthlayer Stage 2 + validation-report UX

**Files:**
- Modify: `Docs/source/xbrl_validation.rst:88-104,134-136,171(after table),215-223,237-238`

**Ground truth:** `axioms/resolver.py:1-8` — "Thin adapter over the canonical truth layer (`truthlayer`)"; `truthlayer/__init__.py:1` — "companyfacts -> DuckDB -> as_of-parameterized reads"; `fetch_ground_truth` returns `Dict[input_name, Optional[float]]` (not a tuple); `FILINGS_DIR` retained only for the sources-popup file server (`resolver.py:23-27`). Frontend renders validation results as a **report conversation turn** + inline failure highlights (`helpers.js:695-698`), and `_statusGlyph` folds any unknown/`ERROR` status into `SKIPPED` display (`helpers.js:701-706`; backend emits per-claim `ERROR` rows, `axioms/__init__.py:74-93`).

- [ ] **Step 1: Replace Stage 2's opening paragraph (lines 88-91):**

```rst
Given a tagged record, Stage 2 looks up the reference value from an
authoritative SEC source. Today's implementation reads from a small local
index of XBRL filings shipped inside the repository at
``Main/backend/mcp_server/xbrl/filings/``.
```
with:
```rst
Given a tagged record, Stage 2 looks up the reference value from an
authoritative SEC source. The resolver (``Main/backend/axioms/resolver.py``)
is a thin adapter over the **truth layer** (``Main/backend/truthlayer/``):
SEC ``companyfacts`` data vendored into the repository, ingested into an
embedded DuckDB store, and queried through an ``as-of``-parameterized,
restatement-aware retrieval API — asking for a value *as of* an earlier date
returns what filings reported at that time, not the latest restated figure.
```

- [ ] **Step 2: Replace the Coverage admonition body (lines 97-102):**

```rst
   Validation currently resolves against **three pre-loaded SEC filings**:
   Apple (FY2023, ``aapl-20230930.xml``), Microsoft (FY2023,
   ``msft-20230630.xml``), and Tesla (FY2023, ``tsla-20231231.xml``). Claims
   about any other ticker or period return ``Skipped`` with a "filing not
   found" reason. Expanding this set is the focus of the upcoming
   :ref:`SEC XBRL Filing Tree <xbrl-filing-tree>` work.
```
with:
```rst
   Validation currently resolves against vendored SEC ``companyfacts`` for
   **three registrants** — Apple, Microsoft, and Tesla — across their
   reported filing history. Claims about any other ticker return ``Skipped``
   with a "no data" reason. Expanding this set is the focus of the upcoming
   :ref:`SEC XBRL Filing Tree <xbrl-filing-tree>` work.
```

- [ ] **Step 3: Replace line 104:**

```rst
The return value is a tuple of *(value, filing accession, filing date)*.
```
with:
```rst
The resolver returns a mapping of the ratio's inputs to their resolved
values; an input resolves to nothing when a required tag is missing, which
downstream stages surface as ``Skipped``. Filing accession and date travel
alongside as provenance.
```

- [ ] **Step 4: Replace "Using Validate" step 4 (lines 134-136):**

```rst
4. **Read the verdict chips** rendered just below the response. Mismatches are
   underlined in red inline so you can locate the offending number in the
   prose without scanning.
```
with:
```rst
4. **Read the validation report.** The verdicts render as a new conversation
   turn summarizing each claim (claimed value vs. filing value), and any
   claimed number that failed verification is highlighted inline in the
   preceding response so you can locate it in the prose without scanning.
```

- [ ] **Step 5: After the Verdict Statuses table (after line 171), add:**

```rst
.. note::
   A claim that errors during evaluation is isolated — one claim's internal
   failure never aborts the rest of the report — and is presented as
   ``Skipped`` in the UI.
```

- [ ] **Step 6: Replace the first Roadmap bullet (lines 215-223):**

```rst
* **SEC XBRL Filing Tree (cloud).** The most visible limitation today is that
  only three filings are bundled with the backend. The next major workstream
  is a **cloud-hosted SEC XBRL Filing Tree** — a structured, query-friendly
  index of XBRL filings keyed by ``(ticker, period, statement)`` — that any
  agent can hit over the network without bundling raw filings into the
  repository. Once it ships, the resolver swaps its filesystem lookup for a
  tree query, and validation extends from three tickers to the full universe
  of SEC registrants. The local-filings path remains as the offline
  fallback for development and air-gapped demos.
```
with:
```rst
* **SEC XBRL Filing Tree (cloud).** The local half of this shipped in
  mid-2026: the **truth layer** (``truthlayer/``) ingests vendored SEC
  ``companyfacts`` into DuckDB and serves as-of-parameterized,
  restatement-aware reads. The remaining workstream is hosting that store as
  a **cloud SEC XBRL Filing Tree** covering the full universe of SEC
  registrants, so any agent can query it over the network without bundling
  data into the repository. Once it ships, the resolver swaps its embedded
  store for a tree query; the local store remains the offline fallback for
  development and air-gapped demos.
```

- [ ] **Step 7: Fix the Further Reading MCP bullet (lines 237-238):**

```rst
* :doc:`mcp_tools` — the MCP tool surface, including ``query_xbrl_filing``
  and ``report_claim`` that this pipeline uses internally.
```
with:
```rst
* :doc:`mcp_tools` — the MCP tool surface, including the ``xbrl-taxonomy``
  server (``lookup_xbrl_tags``, ``validate_xbrl_tag``, ``query_xbrl_filing``)
  that backs Stage 1. (``report_claim`` is a native agent tool, not an MCP
  tool.)
```

- [ ] **Step 8: Build (zero warnings), commit:**

```bash
git commit -am "docs(xbrl): Stage-2 truthlayer rewrite; validation-report UX; roadmap refresh"
```

---

### Task 9: introduction.rst — add the missing flagship features

**Files:**
- Modify: `Docs/source/introduction.rst:6-13` (Key Features list)

- [ ] **Step 1: After the "**Integrated MCP Tools**" bullet (line 10), insert three bullets:**

```rst
- **XBRL Claim Validation**: A deterministic, user-triggered pipeline that checks the numerical claims in agent responses against SEC XBRL filings, with per-claim verdicts and filing provenance (see :doc:`xbrl_validation`).
- **News→Signals Pipeline**: A standalone heartbeat service (``Heartbeat/``) that ingests financial news feeds and publishes dated, deterministic news-sentiment signals for a Dow-30 watchlist, served over ``GET /api/signals/news/``.
- **Discord Concierge**: A Discord chat adapter (``Concierge/``) that brings the same agent into Discord servers.
```

- [ ] **Step 2: Build (zero warnings), commit:**

```bash
git commit -am "docs(intro): add XBRL validation, news-signals, and Discord concierge features"
```

---

### Task 10: project_structure.rst — sync all three trees

**Files:**
- Modify: `Docs/source/project_structure.rst:7-21` (top-level), `:27-91` (backend), `:112-152` (frontend), `:94-106` (highlights)

**Ground truth (repo listings verified 2026-07-12):** top-level adds `Concierge/`, `Heartbeat/`; backend adds `axioms/` (engine, registry, resolver, sources, tool, wrapper, benchmark), `truthlayer/` (contracts, ingest, registry, retrieve, store, data/), `ops/` (egress_firewall), `planner/` (planner, plan, skills), `api/signals_views.py`, `mcp_client/tool_policy.py`, `mcp_server/xbrl/`; frontend adds `claimMarks.js`, `intent.js`, `sse.js`, vendor KaTeX files.

- [ ] **Step 1: Replace the top-level tree block (lines 9-21) with:**

```text
   Agentic-FinSearch/
   ├── .github/                      # CI/CD workflows
   ├── Benchmarks/                   # Benchmark documents and test results
   ├── Concierge/                    # Discord chat adapter for the agent
   ├── Deploy/                       # Deployment configurations (Podman, Caddy)
   ├── Docs/                         # Sphinx documentation (this site)
   ├── Heartbeat/                    # News→signals pipeline (heartbeat + signal builder)
   ├── InternalDocs/                 # Internal documentation (strategy, architecture, QA)
   ├── Main/
   │   ├── backend/                  # Django backend (uv-managed)
   │   └── frontend/                 # Browser extension (bun-managed)
   ├── docker-compose.yml            # Container orchestration
   ├── readthedocs.yml               # ReadTheDocs configuration
   ├── CONTRIBUTING.md               # Contribution guidelines
   └── README.md                     # Project overview
```

- [ ] **Step 2: Backend tree — apply these line edits inside the block (lines 29-91):**

After the `api/` block's `│   ├── views.py` / `│   ├── openai_views.py` lines, insert:
```text
   │   ├── signals_views.py          # News-signals endpoint (GET /api/signals/news/)
```
After the whole `api/` block (i.e. before `├── datascraper/`), insert:
```text
   ├── axioms/                       # XBRL claim-validation engine (registry, engine, resolver)
   ├── truthlayer/                   # XBRL truth layer: vendored companyfacts → DuckDB, as-of reads
```
Inside `mcp_client/`, after `│   ├── tool_wrapper.py` insert:
```text
   │   ├── tool_policy.py            # Deny-by-default MCP tool allow-list
```
Inside `mcp_server/`, after `│   ├── tradingview/` insert:
```text
   │   ├── xbrl/                     # XBRL taxonomy MCP server + bundled filings
```
After the `mcp_server/` block (before `├── prompts/`), insert:
```text
   ├── ops/                          # Operational hardening (egress firewall)
   ├── planner/                      # Agent planning layer and skills
```

- [ ] **Step 3: Backend Highlights (lines 94-106) — append one bullet:**

```rst
* ``axioms/`` + ``truthlayer/`` implement the XBRL validation pipeline documented in :doc:`xbrl_validation`.
```

- [ ] **Step 4: Frontend tree (lines 114-152) — inside `modules/`, after `│   │   ├── backendConfig.js` insert:**

```text
   │   │   ├── claimMarks.js         # Inline XBRL verdict highlights
```
after `│   │   ├── handlers.js` insert:
```text
   │   │   ├── intent.js             # Prompt intent detection
```
after `│   │   ├── sourcesCache.js` insert:
```text
   │   │   ├── sse.js                # Server-Sent-Events streaming client
```
and replace the vendor block:
```text
   │   └── vendor/
   │       └── marked.min.js         # Markdown library
```
with:
```text
   │   └── vendor/
   │       ├── katex.min.js          # Math rendering (KaTeX)
   │       ├── katex-auto-render.min.js
   │       └── marked.min.js         # Markdown library
```

- [ ] **Step 5: Build (zero warnings), commit:**

```bash
git commit -am "docs(structure): sync trees with repo (Concierge, Heartbeat, axioms, truthlayer, ops, planner, frontend modules)"
```

---

### Task 11: installation docs — API-key truth + bun prerequisite

**Files:**
- Modify: `Docs/source/installation/install_agent_with_installer.rst:10,28`
- Modify: `Docs/source/installation/manual_install.rst:14`

**Ground truth:** `entrypoint.sh:73-88` — `REQUIRE_OPENAI_API_KEY` defaults to `1`; the container **exits** if `OPENAI_API_KEY` is empty, printing the `REQUIRE_OPENAI_API_KEY=0` bypass hint. Default model `FinGPT` = Gemini via `GOOGLE_API_KEY` (`models_config.py:58-62`). Frontend builds run through **bun** (`bun install` / `bun run build:full`); no Node prerequisite is expressed anywhere in the frontend config.

- [ ] **Step 1: `install_agent_with_installer.rst:10` — replace:**

```rst
* At least one API key (OpenAI, Anthropic, or DeepSeek)
```
with:
```rst
* An **OpenAI API key** — required by default: the container exits at startup without one (override with ``REQUIRE_OPENAI_API_KEY=0``, not recommended)
* A **Google API key** (``GOOGLE_API_KEY``) for the default ``FinGPT`` (Gemini) model; Anthropic/DeepSeek keys only if you use their models
```

- [ ] **Step 2: `install_agent_with_installer.rst:28` — replace:**

```rst
   Edit ``.env`` and set at least one of ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, or ``DEEPSEEK_API_KEY``.
```
with:
```rst
   Edit ``.env`` and set ``OPENAI_API_KEY`` (required by default — the container refuses to start without it) and ``GOOGLE_API_KEY`` (used by the default ``FinGPT`` model). To run without an OpenAI key, set ``REQUIRE_OPENAI_API_KEY=0`` (not recommended).
```

- [ ] **Step 3: `manual_install.rst:14` — replace:**

```rst
* Node.js 18 (for rebuilding the browser extension)
```
with:
```rst
* Bun (https://bun.sh) — installs dependencies and runs the browser-extension build
```

- [ ] **Step 4: Build (zero warnings), commit:**

```bash
git commit -am "docs(install): correct API-key requirements and bun prerequisite"
```

---

### Task 12: Final verification + PR-2

- [ ] **Step 1: Full build + stale-content sweep**

```bash
cd /mnt/d/fingpt/github/fingpt_rcos
"$SCRATCH/docs-venv/bin/sphinx-build" -b html Docs/source "$SCRATCH/docs-final" 2>&1 | tail -3
grep -rni "mem0\|CONTEXT_MANAGER_MODE" Docs/source/                       # expect empty
grep -rn  "0.13.3" Docs/source/conf.py Docs/source/api_reference.rst     # expect empty (index/updates keep 0.13.x as history)
grep -rn  "Advanced Ask\|Pin-to-Place" Docs/source/                       # expect empty
grep -rn  "Filesystem" Docs/source/mcp_tools.rst Docs/source/usage/  # expect only the disabled-server note from Task 6
```

- [ ] **Step 2: Spot-render** — open `$SCRATCH/docs-final/api_reference.html` and `usage/basic_usage.html`, confirm tables/lists render (no raw RST leakage).

- [ ] **Step 3: Push + open PR-2 (stacked)**

```bash
git push -u origin docs/hosted-docs-refresh
gh pr create --base chore/drop-mem0 --title "docs: hosted-docs refresh — sync Docs/source with 0.16.0 reality" --body "Full accuracy pass over the hosted Sphinx docs against the shipped 0.16.0 codebase (audit + plan: Docs/superpowers/plans/2026-07-12-hosted-docs-refresh.md):

- api_reference: version 0.16.0; auth claims scoped to /v1 (fail-closed prod behavior documented); NEW section documenting all 16 extension/axioms/news-signals endpoints; statelessness note corrected.
- mcp_tools: xbrl-taxonomy server documented; full tool enumerations (SEC-EDGAR 21, Yahoo 9, TradingView 7 crypto-only); filesystem server marked disabled + permanently deny-listed; tool_policy in architecture.
- usage: Ask/Advanced-Ask buttons replaced with the real Thinking/Research mode dropdown + Enter-to-send; Hover in Place/Move with Page labels; Validate button documented.
- xbrl_validation: Stage 2 rewritten for the truthlayer (companyfacts→DuckDB, as-of/restatement-aware); validation-report UX; roadmap acknowledges the shipped local store.
- introduction: XBRL validation, news→signals pipeline, Discord concierge added to Key Features.
- project_structure: trees synced (Concierge, Heartbeat, axioms, truthlayer, ops, planner, frontend modules, KaTeX vendor).
- installation: OpenAI-key-required-by-default truth (REQUIRE_OPENAI_API_KEY), GOOGLE_API_KEY for default model, bun prerequisite.

Stacked on #<PR-1 number> (mem0 drop) — retarget base to main after it merges."
```
(After PR-1 merges, retarget PR-2's base to `main` before merging.)

---

### Task 13: Route endpoint-auth tightening into the security-gate track (Central DB, not this repo)

Per FlyM1ss's decision: the 17 non-`/v1` endpoints (no auth today; rate-limit + cookie-rooted sessions only) get **real bearer-auth tightening** as security-gate work, not a doc-only fix.

- [ ] **Step 1: Read `/mnt/d/CENTRAL-DATABASE/.schemas/spec.yaml`, then write `specs/finsearch-endpoint-auth-01.md`** — acceptance criteria: (a) every non-`/v1` route requires `Authorization: Bearer <FINGPT_API_KEY>` when the key is set, sharing `_authenticate_request` (or an extracted common helper); (b) dev mode (no key) keeps current behavior; (c) prod fails closed like `REQUIRE_FINGPT_API_KEY` does for `/v1`; (d) the Chrome extension keeps working (it must send the header — frontend change included in scope); (e) `Docs/source/api_reference.rst` auth note (Task 4) updated to match; (f) regression tests for allowed/denied paths.
- [ ] **Step 2: Queue it:** `add_task(priority="P1", task_id="finsearch-endpoint-auth-01", project="finsearch", title="Extend bearer-token auth to non-/v1 endpoints (security gate)")`
- [ ] **Step 3: Append a line to the finsearch `project.md` `next_action`** noting the item is queued as part of the pre-launch security gate; `index_file`, validate, commit, push the Central DB.

---

## Discovered follow-ups (logged, OUT of scope)

Resolution pass 2026-07-12 (PRs #350 F-1, #351 F-4, #352 F-3/F-5/F-6/F-7/F-8). Only F-2 remains open.

- **F-1** ~~backend `[dependency-groups] docs` is broken~~ — **RESOLVED, PR #350.** Root cause differed from the log's "old nbconvert" hypothesis: nbconvert was already 7.17.1, but the `[tool.uv]` override `bleach>=6.4.0` REPLACED nbconvert's `bleach[css]` requirement graph-wide, dropping the extra → no `tinycss2` → nbconvert's no-css-sanitizer fallback crashes at import (`NameError: ALLOWED_STYLES`). Fix: override becomes `bleach[css]>=6.4.0`; lock diff is tinycss2 alone. RTD was never affected (`readthedocs.yml` installs `requirements_sphinx.txt` fresh).
- **F-2** (STILL OPEN): "does NOT work on **Brave**" notes (`basic_usage.rst:8-9`, `advanced_usage.rst:8-9`) — unverifiable from code; needs a manual retest before removal.
- **F-3** ~~`:8000` base URL~~ — **RESOLVED, PR #352.** Verified on the droplet: Caddy terminates TLS on 443, gunicorn's 8000 is loopback-bound and firewalled (`ss -tlnp` + firewalld), so every `https://…:8000` example was unreachable. All production URLs in `api_reference.rst` dropped the port; base-URL section explains the proxy split.
- **F-4** ~~legacy `gpt-4o-mini` default~~ — **RESOLVED, PR #351.** Fallback centralized as `models_config.DEFAULT_MODEL = "FinGPT"`; all 4 `views.py` call sites use it; `tests/test_models_config.py` drift-guards both the "default is a configured model" invariant and the call-site pattern.
- **F-5** ~~`mcp_tools.rst` Configuration example~~ — **RESOLVED, PR #352.** `mcpServers`/`disabled` now documented; `transport` bullet removed (loader is stdio-only).
- **F-6** ~~basic_usage Settings wording + project_structure XBRL~~ — **RESOLVED, PR #352.**
- **F-7** ~~memory_system /v1 statelessness cross-ref~~ — **RESOLVED, PR #352.** New `v1-statelessness` label on api_reference's Behavioral Notes; API-Isolation bullet links it.
- **F-8** ~~stale Mem0 line in 2026-02-02 audit~~ — **RESOLVED, PR #352.** Dated-snapshot annotation added (Mem0 removed in #347).
- **F-9** (NEW, logged 2026-07-12): `datascraper/datascraper_refactored.py` is imported nowhere in the backend (checked all non-venv `*.py`) yet carries ~10 `gpt-4o-mini` signature defaults — dead-code candidate; confirm and delete at leisure.

### Post-review fix pass (2026-07-12, PR #348 review)

An 8-angle verified review of the PR diff surfaced six findings; all were fixed in-PR:

1. `index.rst:28` + `updates.rst:11` — stale "three pre-loaded SEC filings (FY2023)" framing replaced with the truth-layer reality (vendored `companyfacts`, three registrants, full reported filing history, as-of-aware).
2. `usage/advanced_usage.rst:59` — TradingView MCP bullet scoped to cryptocurrencies (crypto exchanges only), matching `mcp_tools.rst` and the 6/7 crypto-hardcoded handlers.
3. `api_reference.rst` News Signals caching — corrected to ETag-always / `Last-Modified` unfiltered-only (closes the F-7 ETag item).
4. `introduction.rst:10` — TradingView bullet scoped to crypto TA; XBRL Taxonomy server added (closes the F-6 introduction item).
5. `usage/advanced_usage.rst` troubleshooting — model→env-var mapping now cross-refs the Available Models table; only the stable provider→env-var pairs are stated inline.
6. `mcp_tools.rst` — drift-prone numeric tool counts (21/9/7/3/14) dropped; name lists kept. All five counts were verified correct at time of removal (incl. `sec-edgar-mcp` 1.0.8 via `uv.lock`).
