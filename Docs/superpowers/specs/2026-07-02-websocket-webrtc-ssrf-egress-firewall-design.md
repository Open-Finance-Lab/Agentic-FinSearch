# WebSocket/WebRTC SSRF — container-netns egress firewall

**Date:** 2026-07-02
**Status:** Approved (design) — pending user review of this doc
**Branch base:** `main` (after PR #325 merge, `0cdbc15`)
**Supersedes gap in:** `2026-06-30-ssrf-fetch-efficiency-design.md` §"Out of scope" (WS/WebRTC)

## Summary

The in-browser SSRF route guard (`ssrf_guard.install_route_guard`) pins and byte-caps every
HTTP(S) request Chromium makes, but `page.route` does **not** intercept WebSocket, WebRTC, or
QUIC — Chromium egresses those on its own socket, bypassing the guard entirely. This is the
documented pre-existing gap. We close it at the **network-namespace boundary** with an nftables
egress firewall that drops all traffic (any protocol, any port) to private/link-local/metadata
ranges, applied inside the container's own netns at startup. One boundary, all protocols.

The design and every environment assumption were **validated end-to-end on the production droplet**
with throwaway `--rm` containers before this doc was written (see Appendix A). The privilege model,
the SELinux/rootless-podman nft load, the reachability matrix, the self-disarm defense, and the
`:U,Z` runtime-mount interaction are all empirically confirmed.

## Decisions (locked with the user)

1. **Approach:** OS egress firewall at the netns boundary (deepest altitude; protocol-agnostic).
2. **Fail mode:** fail-closed — the container must not serve without the firewall loaded and proven.
3. **Privilege model:** **root-init-drop.** PID1 runs as root-in-userns (rootless podman keeps it
   host-unprivileged), loads nft with plain `nft` (no setcap), runs an active self-test, chowns the
   runtime mount, then `setpriv`-drops to `uid1001` with **NET_ADMIN removed from the bounding set**
   and `no_new_privs` before running the app. This makes the firewall non-self-disarmable and
   eliminates the setcap-xattr-survival risk. (Rejected: non-root + setcap — self-disarmable by any
   code-exec-as-`fingpt`, and depends on a capability xattr surviving the GHCR round-trip.)
4. **Residual scope:** ship the firewall + disable WebRTC/QUIC at Chromium; **document** the two
   residuals the nft layer cannot close (browser→redis/DNS on the shared netns+uid; `wss://public`
   exfil indistinguishable from HTTPS). Chromium-in-a-separate-netns is future work.
5. **Monitoring:** add a scheduled GitHub Actions cron that curls the public endpoint, so a
   fail-closed outage **without** a deploy (reboot/OOM/kernel update) is caught.

## Background & threat model

The scraper renders attacker-influenceable pages (`navigate_to_url`, `click_element`,
`extract_page_content`, and the sync `scrape_with_playwright`), and Chromium runs **in-process**
and `--no-sandbox`. A rendered page can:
- `new WebSocket('ws://169.254.169.254/…')` → cloud metadata,
- `RTCPeerConnection` ICE/STUN over UDP → internal host/port probing + exfil,
- QUIC/HTTP3 → same, over UDP,

all of which leave on Chromium's own socket and never touch `page.route`. Because Chromium shares
the container's netns, a firewall in that netns is the single choke point for every one of these.

## Deployment facts (verified on the droplet)

- Fedora 42, kernel 6.17.4, **SELinux enforcing**, rootless podman 5.6.2, DigitalOcean droplet.
- Backend container is launched by a systemd `--user` unit:
  `podman run --name fingpt-api --replace --rm --network fingpt-net -v /home/deploy/fingpt/runtime:/app/runtime:U,Z --publish 127.0.0.1:8000:8000 --env-file … --env REDIS_URL=redis://fingpt-redis:6379/0 <image>`
- `fingpt-net` = **10.89.0.0/24**; aardvark DNS resolver at the gateway **10.89.0.1**; `fingpt-redis`
  is a peer in the same /24. The container IP is assigned dynamically (observed 10.89.0.23 → .26
  across restarts — this is why we keep a **/24** accept, not a pinned redis IP).
- Deploy has **no auto-rollback**; `--replace` stops the old container before starting the new one.
- The CI `test` job is `pytest` on ubuntu-latest — it **cannot** reach a container netns, SELinux,
  nft, or the non-root+cap runtime. Anything netns-level is CI-invisible until the deploy
  health-check (the documented #320–#323 failure class).
- `entrypoint.sh` is `#!/usr/bin/env sh` (dash) with `set -eu`; it builds the truthlayer store
  (offline), collectstatic (offline), verifies Playwright, then `exec "$@"` (gunicorn). **No network
  egress happens before gunicorn.**
- `/health/` is a bare 200 view — it touches neither redis nor DNS, so the deploy health-check is
  blind to a functionally-broken-but-up container.

## Architecture

### Privilege model: root-init-drop (validated — Appendix A, probe 2)

`entrypoint.sh` becomes two phases in one file via self-re-exec:

```sh
#!/usr/bin/env sh
set -eu

if [ "$(id -u)" = "0" ]; then
    # ---- root init phase: load + PROVE the egress firewall, then drop all privilege ----
    RULES="$(mktemp)"
    # NB: temp-file, NOT a pipe. `python … | nft -f -` fails OPEN under dash: a generator
    # crash yields empty stdin, nft exits 0, set -e never fires, and we serve with NO firewall.
    python -m ops.egress_firewall > "$RULES" \
        || { echo "FATAL: egress ruleset generation failed" >&2; exit 1; }
    [ -s "$RULES" ] \
        || { echo "FATAL: egress ruleset empty" >&2; exit 1; }
    grep -q '169.254.0.0/16' "$RULES" \
        || { echo "FATAL: metadata drop sentinel missing" >&2; exit 1; }
    grep -qE 'ip6? daddr [0-9a-fA-F].* accept' "$RULES" \
        || { echo "FATAL: own-subnet accept missing" >&2; exit 1; }
    nft -f "$RULES" \
        || { echo "FATAL: nft load failed" >&2; exit 1; }
    rm -f "$RULES"
    nft list table inet ssrf_egress >/dev/null 2>&1 \
        || { echo "FATAL: ssrf_egress table absent after load" >&2; exit 1; }
    # active self-test: proves the DROP bites AND the accept did not strand internal infra
    python -m ops.egress_firewall --self-test \
        || { echo "FATAL: egress firewall self-test failed" >&2; exit 1; }
    # root init chowns the :U,Z runtime mount so the dropped app user can write it
    # (:U chowned it to 0:0 because PID1 is root; validated in Appendix A probe 3)
    chown -R fingpt:fingpt /app/runtime
    # drop to uid1001 with NET_ADMIN removed from the BOUNDING set + no_new_privs, re-exec self
    exec setpriv --reuid=1001 --regid=1001 --init-groups \
         --bounding-set=-net_admin --no-new-privs -- "$0" "$@"
fi

# ---- app phase (uid 1001): the existing startup, unchanged ----
#   OPENAI_API_KEY gate, cache dir, truthlayer store build, collectstatic,
#   Playwright verify, then: exec "$@"
```

Why this is safe and correct (all probe-confirmed):
- Root-in-userns has effective NET_ADMIN (from `--cap-add=NET_ADMIN`) → plain `nft` loads, **no
  setcap needed** → the "does the capability xattr survive GHCR?" failure mode does not exist.
- After `setpriv`, `CapBnd` has the NET_ADMIN bit cleared and `NoNewPrivs=1`, so gunicorn and every
  Chromium child **cannot** `nft flush`/`nft delete` the firewall — proven `BLOCKED_GOOD`.
- The **no-write-under-/app** P0 posture holds: `/app` stays root-owned and read-only to the app;
  only the brief pre-app init is root, and it writes nothing under `/app` except chowning the
  designated writable runtime mount. The long-running app is `uid1001`.

### The ruleset (final form)

`inet` family, single `output` chain, **default-accept with destination drops** (a default-drop
allowlist would break any unforeseen public egress and is far more likely to down prod):

```
table inet ssrf_egress {
  chain output {
    type filter hook output priority 0; policy accept;
    oif "lo" accept
    ip daddr 10.89.0.0/24 accept          # own subnet (discovered): redis + aardvark DNS + gateway
    ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 127.0.0.0/8,
               100.64.0.0/10, 192.0.0.0/24, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4,
               0.0.0.0/8, 255.255.255.255/32 } drop
    ip6 daddr { ::1/128, ::/128, fc00::/7, fe80::/10, ff00::/8, 64:ff9b::/96 } drop
  }
}
```

Deliberate choices, each from the design review:
- **No `ct state established,related accept`.** On a default-accept, dst-DROP, OUTPUT-only chain it
  is a provable no-op (public dsts accepted by policy; private dsts dropped on the first NEW packet
  so no established state to a private dst can form). Removing it drops an untested conntrack/SELinux
  dependency. This is exactly the Stage-C-validated shape.
- **Own-subnet accept is a `/24`, not a pinned redis IP.** The container/redis IPs drift across
  restarts (observed .23→.26); a `/24` survives that. It is emitted **before** the `10.0.0.0/8`
  drop. Trade-off: it also permits browser→any-fingpt-net-peer — a **documented residual** (below);
  narrowing to `redis_ip:6379`+`gw:53` would not close the browser→redis vector anyway (shared
  netns+uid) and would be fragile to redis IP drift.
- **v6 DROP block is a constant**, emitted unconditionally (the netns always has `::1` + a `fe80::`
  on the veth, even on a v4-only network). The `::ffff:0.0.0.0/96` element is dropped as inert
  (v4-mapped destinations egress as real IPv4 packets caught by the v4 drop set; also collapsed at
  the app layer by `ssrf_guard._normalize_ip`). `64:ff9b::/96` (NAT64) is added for future dual-stack.
- **Own-v6 accept** is emitted **only** when v6 subnets are discovered, **one rule per element**
  (never an anonymous `{ }` — an empty set is a parse error → fail-closed prod-down; confirmed in
  Appendix A probe 2 side-B). On today's v4-only `fingpt-net`, no own-v6 accept is emitted.

### `Main/backend/ops/egress_firewall.py` (new)

- `build_egress_ruleset(own_v4_cidrs, own_v6_cidrs) -> str` — **pure, stdlib-only** (`ipaddress`).
  Raises `ValueError` on empty `own_v4_cidrs`. Emits own-subnet accepts **per element** (v4 and v6),
  the constant v4 drop set, and the constant v6 drop block. Never emits `{}`/`{ }`.
- `discover_own_v4()/discover_own_v6()` — parse `ip -o -4/-6 addr show scope global`. Each CIDR must
  `ipaddress.ip_network(strict=False)`, be `.is_private`, and be `subnet_of` one of the **broad**
  RFC1918 ranges (10/8, 172.16/12, 192.168/16). This structurally prevents ever emitting an
  `accept` for a special/metadata range even if a rogue link-scoped route appeared. `EGRESS_OWN_SUBNET`
  env var is a break-glass override (also used to make the CI subprocess test hermetic).
- `main()` — discovers, validates (fail-loud non-zero on empty/implausible), prints the ruleset.
- `--self-test` — runtime checks used by the root init phase:
  - `169.254.169.254:80` TCP connect **must fail/timeout** (proves the DROP bites) → else non-zero.
  - resolve `fingpt-redis` via DNS **must succeed** (proves own-subnet accept covers the gateway
    resolver) → else non-zero.
  - `fingpt-redis:6379` TCP connect **must succeed** within a short retry window (~5×1s, tolerating
    redis's own cold start) → else non-zero.
  - **AMENDED at implementation:** the DNS + redis legs shipped as **advisory (WARN, exit 0)**, not
    fatal — the prior entrypoint had zero redis/DNS boot dependency, so a fatal leg would let a
    redis blip at reboot fail-close a correct firewall (see `_self_test`'s docstring for the full
    rationale). Only the metadata leg is fatal.
- `ops/__init__.py` (new, empty) so `python -m ops.egress_firewall` resolves from WORKDIR `/app`.

### Dockerfile changes

- `apt-get install … nftables iproute2 util-linux` (`util-linux` provides `setpriv`; `iproute2`
  provides `ip`). No setcap.
- **Remove `USER fingpt`** (PID1 becomes root-in-userns; the app is dropped to `uid1001` by the
  entrypoint). Keep the `fingpt` user creation and the `chown` of the image-internal writable dirs
  (`staticfiles/media/logs/tmp cache/runtime`). Update the existing USER/no-write-under-/app comment
  to describe root-init-drop.

### `.github/workflows/backend-deploy.yml` changes

- Add `--cap-add=NET_ADMIN` to the `podman run` in the systemd override, with an inline comment
  marking it **security/functionally load-bearing** (without it, root-in-userns has no NET_ADMIN in
  the bounding set → `nft` EPERM → fail-closed). `:U,Z` is unchanged.
- **Pre-cutover validation gate:** after `podman pull "$REMOTE_IMAGE"` and **before**
  `systemctl --user restart` (the `--replace` that kills the running container), run a throwaway:
  `podman run --rm --cap-add=NET_ADMIN --network fingpt-net --entrypoint sh "$REMOTE_IMAGE" -c
  'python -m ops.egress_firewall > /tmp/r.nft && nft -f /tmp/r.nft && nft list table inet
  ssrf_egress >/dev/null && python -m ops.egress_firewall --self-test'`. **Abort the deploy on
  failure** so the old container keeps serving. This converts a catastrophic mid-swap fail-closed
  into a safe no-op abort and closes the CI-invisible gap.
- Record/echo the previously-deployed image digest before restart, and add a one-line manual
  rollback note to the deploy log for the no-auto-rollback case.

### `docker-compose.yml` change

- Add `cap_add: ["NET_ADMIN"]` to the `api` service, in the **same PR** — otherwise the fail-closed
  entrypoint EPERMs on `nft` and breaks `docker compose up` for every local dev the instant this
  merges. No auto-detect-to-skip (that would fail-open in prod if `--cap-add` were ever dropped).

### Chromium hardening (surface reduction, defense-in-depth)

In both launch sites (`playwright_tools.py` factory args, `url_tools.py:205`), add flags to disable
WebRTC and QUIC (a text scraper needs neither), reducing what the firewall must cover. Exact flag
strings are verified during implementation with a Playwright probe (candidate:
`--disable-features=WebRtcHideLocalIpsWithMdns` + WebRTC/QUIC disables); if launch flags prove
version-flaky, fall back to a `page.add_init_script` that deletes `RTCPeerConnection`/
`webkitRTCPeerConnection` in every frame (definitively kills page-JS WebRTC).

### `.github/workflows/uptime.yml` (new)

Scheduled `cron` (~every 10 min) + `workflow_dispatch`: `curl -fsS --retry 3
https://agenticfinsearch.org/health/`; the job fails (GitHub notifies) if the endpoint is down.
Zero-infra. Documented limitation: GitHub may delay scheduled runs and disables schedules after 60
days of repo inactivity — acceptable for a fallback liveness signal.

## Security argument

- **All protocols, one boundary.** The dst-based drops are protocol-agnostic (TCP and UDP), so
  WS/WebRTC/QUIC/DNS-rebind to any private/metadata target are dropped identically to HTTP.
  Confirmed: metadata REACHABLE → UNREACHABLE with the ruleset applied.
- **Non-self-disarmable.** NET_ADMIN is removed from the app's bounding set and `no_new_privs` is
  set, so a compromised Chromium/gunicorn cannot flush or delete the table (proven `BLOCKED_GOOD`).
- **Genuinely fail-closed.** Temp-file load (not a pipe) + empty/sentinel checks + post-load table
  assertion + active self-test mean a generator crash, empty/partial ruleset, wrong subnet, or
  non-biting DROP all **stop the container**, rather than silently serving unprotected.
- **Deploy-safe.** Pre-cutover gate validates the real image on `fingpt-net` before the `--replace`
  swap; on failure the old container keeps serving. Uptime cron catches non-deploy failures.

## Documented residuals (explicitly NOT closed here)

- **browser→redis:6379 / browser→aardvark-DNS:53.** Chromium shares the app's netns **and** uid1001,
  so no nft rule can distinguish its socket from the legitimate redis/DNS client; any rule that keeps
  redis reachable for the app keeps it reachable for a scraped page. (Mitigations: redis 6379 is
  likely already on Chromium's blocked-port list; add redis AUTH/protected-mode as future
  defense-in-depth; the real fix is running Chromium in a separate netns — future work.)
- **`wss://public-host:443` / WebRTC to public infra.** Egress to public IPs is accepted and
  uncapped (only HTTP(S) is byte-capped/IP-pinned via `page.route`); `wss://attacker:443` is
  indistinguishable from HTTPS at L3/L4. Reduced (not eliminated) by disabling WebRTC/QUIC at
  Chromium. Bounded in practice because fetch/XHR are already IP-pinned+capped, so a page can mostly
  only exfil data it already controls.

## Testing (TDD, RED → GREEN)

**Hermetic unit tests** (`tests/test_egress_firewall.py`):
- All v4 and v6 drop CIDRs present; `169.254.0.0/16` present (metadata).
- Own-subnet accept emitted per-element and positioned **before** the `10.0.0.0/8` drop.
- Output never contains `{}` / `{ }`; v6 drop block present unconditionally (own_v6=[] branch);
  own_v6 accept present only when populated.
- `build_egress_ruleset([], …)` raises.
- Discovery validator **rejects** a special/metadata CIDR (169.254/16, 100.64/…) and accepts a real
  container /24; `EGRESS_OWN_SUBNET` override honored.
- **CIDR parity test** vs `ssrf_guard._is_blocked_ip`: a curated dangerous set (169.254.169.254,
  10/172.16/192.168.x, 127.x, 100.64.x, 0.0.0.0, 255.255.255.255, ::1, fc00::x, fe80::x) is
  firewall-dropped AND (except the own-subnet) guard-blocked. Pin/annotate the Python-version
  dependence of `is_private(100.64.x)`. (Do NOT collapse the two definitions — the firewall must
  deliberately accept the own-subnet the guard blocks.)
- **Subprocess test** running the exact container invocation `python -m ops.egress_firewall` (with
  `EGRESS_OWN_SUBNET` set for hermeticity, `cwd=BACKEND_DIR`): returncode 0, non-empty stdout. This
  catches the module-path/invocation trap a plain import test would miss.

**Pre-merge droplet load** — the full production-shape load + reachability matrix + negative
self-test is exercised by the deploy pre-cutover gate on every deploy; it was also validated
manually before this doc (Appendix A). Re-run once against the actually-built image (workflow_dispatch
or a throwaway `podman run`) before first prod cutover.

**Regression** — full backend suite green (`pytest`), matching the PR #325 baseline (554 passed).

## Rollout plan

1. Land the code (generator + tests + Dockerfile + entrypoint + compose + Chromium flags + uptime
   cron) so CI is green. The entrypoint's fail-closed nft load is inert in CI (pytest never runs it).
2. The workflow's `--cap-add` + pre-cutover gate land together (same PR) — a partial landing would
   reproduce the CI-invisible outage class.
3. First prod deploy: the pre-cutover gate runs the real image on `fingpt-net`; if it fails, the
   deploy aborts and the current container keeps serving. If it passes, the swap proceeds and the
   post-swap `/health/` gate confirms.
4. Keep the previous image digest handy for one-command manual rollback (no auto-rollback exists).

## Packaging

One branch off `main`. Suggested commits (single PR, "close the WS/WebRTC SSRF gap"):
1. `feat(security): egress_firewall ruleset generator + tests`
2. `feat(security): root-init-drop entrypoint + Dockerfile (load egress firewall, drop NET_ADMIN)`
3. `ci(security): --cap-add=NET_ADMIN + pre-cutover firewall validation gate; compose cap_add`
4. `feat(security): disable WebRTC/QUIC at Chromium; uptime monitor; docs`

Planning docs (this spec + the implementation plan) land as a separate `docs(security)` commit to
`main` first, per repo convention.

## Out of scope

- Running Chromium in a separate network namespace / sidecar off `fingpt-net` (the only thing that
  closes browser→internal SSRF) — future design.
- redis AUTH/protected-mode — future defense-in-depth.
- Any change to `safe_get` / the existing HTTP route-guard behavior.

## Appendix A — droplet probe evidence (throwaway `--rm` containers; prod undisturbed)

**Probe 1 (feasibility):** rootless `--cap-add=NET_ADMIN` container loads nftables under SELinux
enforcing — root-in-container `NFT_LOAD_OK`; non-root uid1001+setcap `NONROOT_NFT_OK`. Reachability
with a v4 ruleset on `fingpt-net`: metadata REACHABLE→UNREACHABLE while dns/redis/public stayed up.

**Probe 2 (recommended architecture, root-init-drop):** full v4+v6 ruleset loaded as root under
SELinux on `fingpt-net`; root self-test passed; `setpriv` dropped to `uid1001` with `CapBnd`
NET_ADMIN bit cleared and `NoNewPrivs=1`; `nft flush` and `nft delete table` both `BLOCKED_GOOD`;
post-drop reachability still correct. Side checks: no `--cap-add` → `nft EPERM` (cap load-bearing);
`ip6 daddr {} accept` → parse error (empty-set trap); discovery on real `fingpt-net` → `10.89.0.26/24`,
single scope-link route, no rogue 169.254 route, v6 = only `::1`+`fe80::` (v4-only).

**Probe 3 (`:U,Z` mount interaction):** with root PID1, `:U` chowned `/app/runtime` to `0:0` and
`uid1001` could NOT write it (`FAIL`); after root init `chown -R 1001:1001 /app/runtime`, `uid1001`
wrote and read the mount fine (`WRITE_RUNTIME_AS_UID1001=OK`, file owned `1001:1001`). Confirms the
explicit chown is required and sufficient; `:U,Z` suffixes stay unchanged.
