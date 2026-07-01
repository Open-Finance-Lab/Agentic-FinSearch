# Pre-Launch Security Audit & Remediation Spec

**Date:** 2026-06-29
**Status:** draft (findings final; remediation designs pending per-item approval)
**Scope:** Full-app security audit of `fingpt_rcos` ahead of opening Agentic FinSearch to a public community.
**Provenance:** 14-surface multi-agent audit (find → adversarially verify → synthesize). Run partially interrupted by a host OOM; 14/14 finder lanes + 43/63 verifier verdicts recovered from the workflow journal, the rest corroborated by cross-lane duplicates and hand-verification. Raw data: `tmp/security-audit-2026-06-29/`.

---

## Context

Agentic FinSearch is a Django backend that drives an LLM agent over 5 MCP tool servers, plus a Chrome extension frontend and two Discord bots, deployed on one DigitalOcean droplet behind Caddy (TLS/HSTS edge) via a podman pod (host firewall: ssh/http/https only). The chat endpoints are **intentionally public/loginless** so the community can use them.

**Threat model:** primary adversaries are (a) an anonymous internet user and (b) a malicious/curious community member. "The endpoint is public" is by design — the question this audit answers is *what damage a public or malicious caller can actually do, and how far the blast radius reaches.*

**Result:** 59 real findings (4 refuted) — **5 Critical, 18 High, 14 Medium, 19 Low, 3 Info**; 29 reachable by an anonymous internet user. They collapse into **7 root causes**; fixing the roots resolves ~40 findings.

---

## The 7 root causes

| ID | Root cause | Severity reach |
|----|-----------|----------------|
| **A** | Public agent has a **writable `filesystem` MCP rooted at `/app`**, container runs as **root**, no server-side tool allow-list (`tools_allowed=None` fallback attaches everything) | RCE-as-root / full source+config read |
| **B** | **No code-level SSRF egress control** on scrape / playwright / `auto_scrape` | Cloud metadata + pod-internal access, anon |
| **C** | **No spend/abuse controls**: unauth + caller-chosen `session_id` + per-IP limit broken behind Caddy + no global cost/concurrency cap + 600 s/21-min timeouts + OpenAI API fails open | Unbounded LLM bill, trivial DoS, cross-user chat read |
| **D** | **Indirect prompt injection** — tool/scrape outputs returned to the agent as trusted instructions | Remote driver for A & B |
| **E** | **Frontend XSS** — blocklist sanitizer (`html:true`) + KaTeX `trust:true` | Cookie/backend-URL exfil per user |
| **F** | **CI/CD** — backend deploy not `main`-gated, mutable action tags, mutable `:main` image pull | Arbitrary-branch deploy to prod |
| **G** | **Secrets/PII hygiene** — full env into every MCP child, query-string PII in access logs, token `.env` not git-ignored | Token leak / surveillance / blast-radius amplification |

---

## Findings inventory (grouped by root cause)

### Root A — Agent filesystem & tool privilege
- **[Critical] endpoint-authz-1** — Unauth chat endpoints drive an LLM with RW filesystem MCP over `/app`, as root. `api/views.py:219-307,430-514,518-667`; `mcp_client/agent.py:179-200`; `mcp_server_config.json:3-10`; `Dockerfile` (no USER). *(verifier-confirmed)*
- **[Critical] prompt-injection-1** — Filesystem MCP attached with no server-side tool allow-list. `mcp_server_config.json:3-9`; `mcp_client/mcp_manager.py:185-248`; `mcp_client/agent.py:129-216`; `planner/skills/web_research.py:13`; `datascraper/datascraper.py:1102-1110`. *(confirmed)*
- **[Critical] agent-mcp-fs-1** — filesystem MCP grants public agent RW to all `/app`. `mcp_server_config.json:3-10`; `agent.py:131-216`; `datascraper.py:1338-1393`. *(corroborated)*
- **[Critical] agent-mcp-fs-2** — Default/fallback skill attaches full toolset (`tools_allowed=None`); dangerous tools only hidden from the prompt catalog, not scoped out. `planner/skills/web_research.py:13-18`; `planner/skills/registry.py:35-45`; `agent.py:210-216`; `datascraper.py:1311-1345`. *(corroborated)*
- **[High] secrets-config-1** — Public agent gets filesystem RW over entire source tree as root. *(confirmed)*
- **[High] agent-mcp-fs-4** — `.env`/secrets + WhiteNoise staticfiles live under the writable MCP root. **Nuance (verifier):** `.env` is `.dockerignore`d so not in the prod image; real risk is **write-to-source** + **process-env** secret exposure, not `cat /app/.env`. `settings.py:20-24`, `settings_prod.py` STATIC_ROOT.
- **[Medium] agent-mcp-fs-3** — root container + gunicorn `umask=0` ⇒ MCP writes have no OS barrier. `Dockerfile`; `gunicorn.conf.py:30`.
- **[Medium] container-deploy-1** — Container runs as root (no USER). `Dockerfile`.

### Root B — SSRF egress
- **[High] ssrf-scraping-1** — `/api/auto_scrape` server-side fetch of attacker URL, no SSRF guard. `api/views.py:831-879` → `datascraper/url_tools.py:225-292`. *(confirmed; body is fed to context, not echoed verbatim)*
- **[High] ssrf-scraping-2** — Playwright `navigate_to_url`/`click_element`/`extract_page_content` navigate arbitrary URLs (incl. `file://`). `datascraper/playwright_tools.py:73-280`; `agent.py:135-136`. *(confirmed; GET route)*
- **[High] ssrf-scraping-3** — `scrape_url` agent tool fetches arbitrary URLs (redirect/size/DNS-rebind). `url_tools.py:295-342,225-292`; `views.py:432,520`. *(confirmed)*
- **[High] endpoint-authz-3** — Unauth SSRF via `auto_scrape` / OpenAI `url` param. `views.py:831-879`; `openai_views.py:247-261`; `url_tools.py:225-237`. *(confirmed; openai path is auth-gated only if FINGPT_API_KEY set — see C)*
- **[High] agent-mcp-ssrf-1** — Browser/scrape tools reach metadata + pod-internal services. *(confirmed)*
- **[High] prompt-injection-3** — Soft prompt-only domain restriction is the only SSRF control. `prompt_builder.py:107-112`; `prompts/core.md:50`. *(confirmed; the prompt guard is even weaker than claimed)*
- **[Medium] ssrf-scraping-4** — No redirect/size/DNS-rebind protection (guard-bypass). `url_tools.py:237,240,263`. *(downgraded High→Medium)*

### Root C — Spend / abuse / auth / session
- **[Critical] dos-cost-1** — Unauth endpoints drive an expensive ≤30-turn multi-tool agent with **no global cost/concurrency/budget cap**. `views.py:518-571,219-307`; `datascraper.py:1328-1393`. *(corroborated)*
- **[High] dos-cost-2** — `@ratelimit(key='ip')` behind Caddy ⇒ all users share one bucket (community-wide self-DoS); direct-port deploy makes it per-IP & multipliable. `views.py:141,164,190,220,311,431,519,671`; `settings.py:153`; `Caddyfile.example:18-21`. *(hand-verified)*
- **[High] dos-cost-4** — `/v1/chat/completions` + `/v1/models` **unauthenticated by default** (FINGPT_API_KEY empty in env examples; auth fails open). `openai_views.py:64-97,124-158`. *(hand-verified at `openai_views.py:73`)*
- **[High] dos-worker-3** — gthread `threads=1`, `GUNICORN_WORKERS=1`, `timeout=600` ⇒ 1–2 slow requests down the service. `gunicorn.conf.py:6-12`; `Dockerfile:62-65`.
- **[High] data-pii-session-idor-1** — Conversation-history IDOR: caller-supplied `session_id` is the cache key, no cookie binding. `views.py:97-114`; `datascraper/unified_context_manager.py:121-122`. *(hand-verified at `views.py:97`)*
- **[High] discord-bots-2** — No global throttle + 21-min timeout + loopback origin ⇒ amplified DoS. `Concierge/concierge/{ratelimit.py:16-58,config.py:12-16,finsearch_client.py:107-116}`; `views.py:141`.
- **[Medium] endpoint-authz-6** — State-mutating/tool-invoking endpoints lack rate limiting (`auto_scrape`, `add_webtext`, `clear`, `get_memory_stats`, preferred-urls). `views.py:831,882,935,965,1039,1046`.
- **[Medium] endpoint-authz-4** — Caller-chosen `session_id` enables cross-session clear/poison. `views.py:97-114,935-962`; `context_integration.py`.
- **[Medium] dos-cost-5** — Caller `session_id` + untrimmed history re-sent each turn = quadratic cost. `views.py:97-114`; `unified_context_manager.py:189-238`.
- **[Medium] discord-bots-3** — Backend session keyed on guessable public Discord ids w/ `use_memory=true`. `Concierge/concierge/{session.py:6-11,handlers.py:24-35}`.
- **[Low] endpoint-authz-2** — OpenAI Bearer auth silently disables when key unset (prod footgun twin of dos-cost-4).

### Root D — Indirect prompt injection
- **[Critical] prompt-injection-2** — Scraped/navigated page text returned to the agent as unwrapped, trusted tool result. `mcp_client/tool_wrapper.py:100-115`; `url_tools.py:225-342`; `playwright_tools.py:73-280`; contrast `prompt_builder.py:43-47,125-127` (only static system_prompt is defanged). *(confirmed; severity upgraded High→Critical)*
- **[Low] prompt-injection-4** — `core.md` tool catalog hides attached tools / falsely asserts none exist (false sense of containment).

### Root E — Frontend XSS
- **[High] frontend-extension-1** — Blocklist sanitizer + markdown-it `html:true` allows `javascript:` URI XSS in rendered LLM/source content. `Main/frontend/src/modules/markdownRenderer.js:107-112,137-162,232,252`.
- **[High] frontend-extension-2** — KaTeX `trust:true` renders attacker `\href{javascript:}`/`\includegraphics`, bypassing the sanitizer. `markdownRenderer.js:21-40,172-179,235,255`.
- **[Medium] frontend-extension-3** — Backend API base overridable from host-page global / page-origin localStorage, no allowlist. `Main/frontend/src/modules/backendConfig.js:7-50`.
- **[Low] frontend-extension-4/5/6** — No extension CSP / Shadow DOM isolation; `web_accessible_resources` to `all_urls` (fingerprinting); model description via `innerHTML`.

### Root F — CI/CD & supply chain
- **[High] cicd-supplychain-2** — Backend deploy job lacks the `if: github.ref=='refs/heads/main'` gate its siblings have ⇒ `workflow_dispatch` from any branch builds/deploys to prod. `.github/workflows/backend-deploy.yml:15,127-135`. *(confirmed)*
- **[Medium] cicd-supplychain-1** — Actions pinned to mutable tags (not SHAs) despite in-file "SHA-pinned" claims. `backend-deploy.yml:42,45,73,103,111`; `concierge-tests.yml:33,34`; `heartbeat-tests.yml:31,56`.
- **[Medium] cicd-supplychain-3** — Droplet pulls mutable `:main` image (not `:sha` digest); no signing/provenance. `backend-deploy.yml:153,166,174`.
- **[Low] cicd-supplychain-4** — No GitHub Environment protection on deploy jobs (DEPLOY_SSH_KEY = prod-root SSH key).

### Root G — Secrets / PII / logging hygiene
- **[Medium] secrets-config-2** — Entire secret-laden process env copied into every MCP child (incl. npm filesystem server). `mcp_client/mcp_manager.py:138-159`.
- **[Medium] discord-bots-1** — Live `DISCORD_BOT_TOKEN` env files not covered by any `.gitignore`. `.gitignore`; `Concierge/.gitignore`; `Heartbeat/` (none).
- **[Medium] data-pii-accesslog-querystring-1** — gunicorn access log records full request line ⇒ captures questions, URLs, `session_id`s from GET query strings. `gunicorn.conf.py:16-19` + GET chat endpoints.
- **[Low] secrets-config-3** — Gemini error hook writes full unredacted LLM payload to a predictable path.
- **[Low] discord-bots-5** — Shared secret env files rely on manual `chmod 600`.
- **[Low] discord-bots-4** — LLM answer rendered to Discord without markdown escaping (masked-link phishing).
- **[Low] data-pii-logging-fullquestion-1 / data-pii-currenturl-applog-1 / data-pii-llmdebug-footgun-1 / data-persist-plaintext-cache-1** — full question logged unrated; `current_url` logged at INFO; `log_llm_payload` dumps PII; plaintext pickle conversation cache.

### Misc (Low/Info, defense-in-depth)
- **[Medium] container-deploy-2** — compose & docs publish `8000` on `0.0.0.0` (documented path bypasses Caddy). `docker-compose.yml:8-9`; `Docs/production_setup.md`.
- **[Medium] container-deploy-4** — gunicorn 600 s timeout + low worker_connections ⇒ slowloris.
- **[Low] django-hardening-1/2** — hardcoded credentialed CORS origins; `SESSION_COOKIE_SAMESITE='None'` + `CORS_ALLOW_CREDENTIALS=True`.
- **[Low] endpoint-authz-5/7/9** — anonymous preferred-URL mutation; state-changing GETs; `debug/memory` registered in prod URLConf (fails closed only if `DEBUG=False`).
- **[Low] container-deploy-5** — no read-only rootfs / cap-drop / pids-limit / compose memory limit.
- **[Info] path-traversal-fileio-1** — `xbrl_filing_download` filename regex allows trailing newline (NOT exploitable; regex + resolved-dir check hold). `views.py:159-160`.
- **[Info] container-deploy-6** — compose mounts host source RW despite "read-only" comment.
- **[Info] dependency-cve-1** — Concierge deps unpinned; aiohttp floor admits CVE-affected versions.

### Refuted (verified false / overstated — dropped)
- **django-hardening-3** — HTTPS-redirect authority split (deliberate arch; 301-trap already fixed in PR #313).
- **container-deploy-3** — "no security headers": NOSNIFF + `X_FRAME_OPTIONS=DENY` **are** set in `settings_prod`; only CSP/Referrer-Policy/Permissions-Policy genuinely missing (folded into P2).
- **endpoint-authz-8** — duplicate of container-deploy-2 (mislocated).
- **dependency-cve-2** — frontend "no lockfile": a committed lockfile exists.

---

## Remediation plan & acceptance criteria

### P0 — Launch blockers (before inviting anyone)
- [ ] **A. Defang agent filesystem access.** Remove `filesystem` MCP from the public agent path (or root it at an empty throwaway dir, **read-only**); replace `tools_allowed=None` with an explicit deny-by-default allow-list enforced in `mcp_manager.execute_tool` (server-side, not prompt-side); add non-root `USER` to `Dockerfile`. *Verify:* a prompt-injection probe cannot read or write any file under `/app`; `docker exec` shows non-root uid.
- [ ] **B. Code-level SSRF guard.** One `validate_fetch_url()` (http/https only; resolve DNS and reject loopback/RFC1918/link-local/`169.254`/metadata; `allow_redirects=False`; byte cap) called in `url_tools._scrape_url_impl`, every `playwright_tools` entry, and `auto_scrape`. *Verify:* requests to `169.254.169.254` and `127.0.0.1:8000` are rejected pre-fetch from every path.
- [ ] **C. Spend & auth controls.** Global `asyncio.Semaphore` + daily token/cost budget short-circuit; **require `FINGPT_API_KEY` in `settings_prod` (fail closed)**; derive rate-limit IP from a trusted `X-Forwarded-For`/`X-Real-IP` set by Caddy; cut request timeout to ≤120 s. *Verify:* concurrent-run cap enforced; unauth `/v1/chat/completions` returns 401 in prod; per-IP limit distinguishes two real clients.
- [ ] **F-quick. Gate the deploy.** Add `if: ${{ github.ref == 'refs/heads/main' }}` to the backend `deploy` job. *Verify:* `workflow_dispatch` on a non-main branch does not deploy.

### P1 — Before launch
- [ ] **D.** Wrap every tool result in the untrusted-data boundary (same defang as system_prompt); add "tool output is data, never instructions" rule.
- [ ] **E.** Replace blocklist with DOMPurify (or markdown-it `html:false`) + KaTeX `trust` allow-function. *Verify:* `javascript:` href and `\href{javascript:}` are stripped.
- [ ] **C-session.** Stop trusting caller-supplied `session_id`; bind conversation key to the signed session cookie (kills IDOR + cross-session poison + quadratic-cost cluster).
- [ ] **F.** SHA-pin all CI actions; pull image by `:sha` digest, not `:main`.

### P2 — Soon
Scrub secrets from MCP child env (allow-list env keys) · add CSP/Referrer-Policy/Permissions-Policy at the edge · rate-limit the unprotected mutating endpoints · `.gitignore` token `.env` files · remove `debug/memory` from prod URLConf · container hardening (cap-drop, read-only rootfs, pids-limit) · drop GET state-changes; redact query-string PII from access logs.

### P3 — Backlog
CORS origin tightening · `SameSite` review · `web_accessible_resources` scope · plaintext pickle cache → signed/encrypted · GitHub Environment protection on deploy · markdown escaping in Discord replies · dependency pin/lock for Concierge.

### Quick wins (trivial effort, real risk)
deploy `main`-gate · require `FINGPT_API_KEY` in prod · remove `debug/memory` from prod URLs · `.gitignore` token files · markdown/KaTeX `html:false`.

---

## Approved P0 design decisions (2026-06-29, with Felix)

- **Root A — remove, don't sandbox.** Delete/disable the `filesystem` MCP (`mcp_server_config.json`) — unused by any legitimate flow (only docstring mentions). Add a **server-side deny-by-default tool allow-list** in `mcp_client/mcp_manager.py:execute_tool`: every skill (incl. the `web_research` fallback) declares a finite tool list; no `tools_allowed=None` reaches the public agent. Add a non-root `USER` to the Dockerfile. Layered so even a re-introduced MCP is contained.
- **Root B — denylist floor + auth seam.** `validate_fetch_url()` blocks loopback/RFC1918/link-local/`169.254` metadata, http(s)-only, `allow_redirects=False`, byte cap; applied in `url_tools`, `playwright_tools`, `auto_scrape`. Agent keeps arbitrary public-finance reach. **Forward-compat (Felix):** a **user-login system is planned** — build the request path with an explicit auth/identity seam so the Root-C caps refactor from per-IP to **per-user** without re-plumbing. Align with the reserved `atl_account_id` two-layer identity in the Concierge design.
- **Root C — bound blast radius, keep main chat loginless for now.**
  - Global `asyncio.Semaphore` = **3 concurrent** agent runs (env `AGENT_MAX_CONCURRENCY`).
  - Daily global budget = **2000 agent-runs/day** (env `AGENT_DAILY_RUN_BUDGET`), counter in shared cache; **503 + Retry-After** beyond it. (Token/$ budget can layer on later.)
  - Rate-limit IP = Caddy's trusted client IP (`X-Real-IP` / validated leftmost `X-Forwarded-For`); keep `@ratelimit` but make the key function **swappable to per-user** when login lands.
  - gunicorn timeout 600 → **120 s**; raise gthread `threads`.
  - **Require `FINGPT_API_KEY` in `settings_prod`** (fail closed); `/v1/*` → 401 without Bearer. Main `/get_chat_response_stream/` stays anonymous, bounded by the caps above.
- **Root F-gate — one-liner.** Add `if: ${{ github.ref == 'refs/heads/main' }}` to the backend deploy job.
- **Build order (test-gated):** F-gate → A → B → C. Implement in small batches — **≤50 agents/subagents** (this 16GB laptop OOM-froze at ~71).

## Constraints
- Public chat stays loginless (community design, *for now* — a user-login system is planned; leave the seam) — fixes must reduce *damage*, not gate legitimate use.
- Do not regress the PR #313 301-trap fix; the architecturally-correct follow-up (`SECURE_SSL_REDIRECT=False`, Caddy as sole HTTPS authority) is an env-only change.
- Backend request contract must not change (Concierge + extension both depend on it) unless coordinated.

## References
- Recovered audit data: `tmp/security-audit-2026-06-29/{findings-merged.json,journal.jsonl}`
- Central DB: queue tasks `finsearch-security-*`; `knowledge/finsearch-security-audit-2026-06.md`; decision 2026-06-29.
- Prior anchor: session memory `finsearch-concierge-backend-security.md` (the deferred review this audit fulfils).
