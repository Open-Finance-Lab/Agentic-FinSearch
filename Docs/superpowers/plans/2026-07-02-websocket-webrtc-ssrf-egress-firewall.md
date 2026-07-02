# WebSocket/WebRTC SSRF Egress Firewall — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the WebSocket/WebRTC/QUIC SSRF gap `page.route` cannot intercept, by loading an nftables egress firewall into the backend container's own network namespace at startup.

**Architecture:** `entrypoint.sh` runs as root-in-userns, generates + loads (fail-closed) an `inet` nftables ruleset that DROPs egress to all private/link-local/metadata ranges (any protocol/port) while accepting the container's own /24 (redis + podman DNS) and public destinations, runs an active self-test, chowns the runtime mount, then `setpriv`-drops to uid1001 (NET_ADMIN removed from the bounding set, `no_new_privs`) before running the app. Defense-in-depth: WebRTC/QUIC disabled at Chromium; an off-box uptime cron. Full design + droplet validation: `Docs/superpowers/specs/2026-07-02-websocket-webrtc-ssrf-egress-firewall-design.md`.

**Tech Stack:** Python 3.12 (stdlib `ipaddress`/`socket`/`subprocess`), nftables (`inet` family), `setpriv` (util-linux), rootless podman 5.6.2 / SELinux-enforcing Fedora 42, Playwright 1.58 Chromium, GitHub Actions, pytest.

**Base branch:** `main` @ `0b04dbb`. **New feature branch:** `security/ws-webrtc-egress-firewall`.

**Working dir for all paths below:** `/mnt/d/fingpt/Github/fingpt_rcos`. Backend root: `Main/backend`. Python: `Main/backend/.venv/bin/python`, tests: `Main/backend/.venv/bin/pytest`.

---

## File Structure

- **Create** `Main/backend/ops/__init__.py` — empty package marker so `python -m ops.egress_firewall` resolves from WORKDIR `/app` and pytest can import `ops.egress_firewall`.
- **Create** `Main/backend/ops/egress_firewall.py` — pure ruleset generator (`build_egress_ruleset`) + discovery/validation + `main()` + `--self-test`. Single responsibility: everything about the egress ruleset and its runtime proof.
- **Create** `Main/backend/tests/test_egress_firewall.py` — hermetic unit tests for the generator, discovery validation, parity vs `ssrf_guard`, and the subprocess invocation.
- **Modify** `Main/backend/entrypoint.sh` — root-init-drop: guarded firewall load + self-test + runtime chown + `setpriv` drop, then the existing app-phase startup.
- **Modify** `Main/backend/Dockerfile` — install `nftables iproute2 util-linux`; remove `USER fingpt` (init drops instead); update the USER/no-write-under-/app comment. No setcap.
- **Modify** `.github/workflows/backend-deploy.yml` — `--cap-add=NET_ADMIN` (load-bearing comment) + a pre-cutover validation gate that aborts before the `--replace` swap on failure.
- **Modify** `docker-compose.yml` — add `cap_add: ["NET_ADMIN"]` to the `api` service.
- **Modify** `Main/backend/datascraper/playwright_tools.py` — Chromium WebRTC/QUIC hardening in the async factory.
- **Modify** `Main/backend/datascraper/url_tools.py` — same hardening in the sync Playwright path (~L205).
- **Modify** `Main/backend/tests/test_ssrf_wire.py` — assert the Chromium hardening wiring.
- **Create** `.github/workflows/uptime.yml` — scheduled public-endpoint liveness curl.

---

## Task 0: Branch

- [ ] **Step 1: Create the feature branch**

```bash
cd /mnt/d/fingpt/Github/fingpt_rcos
git switch -c security/ws-webrtc-egress-firewall
```

---

## Task 1: Pure ruleset generator + unit tests (TDD)

**Files:**
- Create: `Main/backend/ops/__init__.py`
- Create: `Main/backend/ops/egress_firewall.py`
- Test: `Main/backend/tests/test_egress_firewall.py`

- [ ] **Step 1: Create the package marker**

```bash
: > Main/backend/ops/__init__.py
```

- [ ] **Step 2: Write the failing generator tests**

Create `Main/backend/tests/test_egress_firewall.py`:

```python
"""Egress firewall ruleset-generator tests (WS/WebRTC SSRF).

Pure/hermetic: build_egress_ruleset takes CIDRs and returns a string; no network,
no nft, no container. The one subprocess test uses EGRESS_OWN_SUBNET so it does not
depend on the runner's interfaces.
"""
import ipaddress
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ops.egress_firewall import (
    _V4_DROP,
    _V6_DROP,
    _valid_own,
    build_egress_ruleset,
    discover_own_v4,
)
from datascraper import ssrf_guard


def test_v4_drop_ranges_all_present():
    rs = build_egress_ruleset(["10.89.0.0/24"])
    for cidr in _V4_DROP:
        assert cidr in rs


def test_v6_drop_block_present_even_on_v4_only_network():
    rs = build_egress_ruleset(["10.89.0.0/24"], [])
    for cidr in _V6_DROP:
        assert cidr in rs


def test_metadata_range_is_dropped():
    assert "169.254.0.0/16" in build_egress_ruleset(["10.89.0.0/24"])


def test_own_subnet_accept_precedes_broad_drop():
    rs = build_egress_ruleset(["10.89.0.0/24"])
    assert rs.index("ip daddr 10.89.0.0/24 accept") < rs.index("10.0.0.0/8")


def test_never_emits_empty_anonymous_set():
    rs = build_egress_ruleset(["10.89.0.0/24"], [])
    assert "{}" not in rs
    assert "{ }" not in rs


def test_v6_own_subnet_emitted_per_element_only_when_present():
    assert "ip6 daddr" not in build_egress_ruleset(["10.89.0.0/24"], []).split("ip6 daddr {")[0]
    rs = build_egress_ruleset(["10.89.0.0/24"], ["fd00:89::/64"])
    assert "ip6 daddr fd00:89::/64 accept" in rs


def test_empty_own_v4_raises():
    with pytest.raises(ValueError):
        build_egress_ruleset([])


def test_valid_own_rejects_special_and_public_ranges():
    assert _valid_own(["169.254.0.0/16"]) == []      # metadata / link-local
    assert _valid_own(["100.64.0.0/10"]) == []       # CGNAT (not nested in broad RFC1918)
    assert _valid_own(["8.8.8.0/24"]) == []          # public
    assert [str(n) for n in _valid_own(["10.89.0.26/24"])] == ["10.89.0.0/24"]


def test_override_env(monkeypatch):
    monkeypatch.setenv("EGRESS_OWN_SUBNET", "10.89.0.0/24")
    assert [str(n) for n in discover_own_v4()] == ["10.89.0.0/24"]


# Parity vs ssrf_guard: the two "what is private" definitions must not silently diverge.
# 100.64/10 (CGNAT) is deliberately EXCLUDED: the firewall drops it, but ssrf_guard's
# ipaddress-based check does NOT block it on Python 3.12/3.13 (is_private=False) -- a
# real HTTP-side gap tracked separately, NOT fixed in this egress-firewall PR.
_DANGEROUS = [
    "169.254.169.254", "10.1.2.3", "172.16.5.5", "192.168.1.1", "127.0.0.1",
    "0.0.0.0", "255.255.255.255", "198.18.0.1", "192.0.0.170", "240.0.0.1",
    "::1", "fc00::1", "fe80::1",
]


def test_parity_with_ssrf_guard_block_list():
    rs = build_egress_ruleset(["10.89.0.0/24"])
    for ip in _DANGEROUS:
        assert ssrf_guard._is_blocked_ip(ip), f"ssrf_guard should block {ip}"
        addr = ipaddress.ip_address(ip)
        drops = _V4_DROP if addr.version == 4 else _V6_DROP
        assert any(addr in ipaddress.ip_network(c) for c in drops), f"firewall should drop {ip}"


def test_subprocess_invocation_is_hermetic():
    env = dict(os.environ, EGRESS_OWN_SUBNET="10.89.0.0/24")
    backend = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "-m", "ops.egress_firewall"],
        cwd=backend, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "table inet ssrf_egress" in r.stdout
    assert "169.254.0.0/16" in r.stdout
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd Main/backend && .venv/bin/pytest tests/test_egress_firewall.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops.egress_firewall'`.

- [ ] **Step 4: Write `ops/egress_firewall.py`**

Create `Main/backend/ops/egress_firewall.py`:

```python
"""Container network-namespace egress firewall for the SSRF boundary.

Chromium (Playwright) runs in-process and egresses WebSocket/WebRTC/QUIC on its own
socket, which page.route (and thus ssrf_guard's HTTP route guard) cannot intercept.
This module generates an nftables `inet` ruleset -- loaded into the container's own
netns at startup by entrypoint.sh, as root, before dropping to uid1001 -- that DROPs
egress to every private / link-local / metadata / special range for ALL protocols and
ports, while ACCEPTing the container's own subnet (redis + the podman DNS resolver +
gateway) and public destinations. One boundary, all protocols.

build_egress_ruleset is pure + stdlib-only so the security-critical ruleset is
hermetically unit-testable. Discovery + validation live in main(); the active runtime
self-test lives behind --self-test.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

TABLE = "ssrf_egress"

# A legitimate container subnet must nest inside one of these broad RFC1918 ranges.
# A discovered own-subnet is accepted ONLY if it does, so a rogue link-scoped route
# into a special/metadata range can never become an `accept`.
_BROAD_RFC1918 = tuple(
    ipaddress.ip_network(c) for c in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)

# IPv4 destinations that must never be reachable. Mirrors ssrf_guard._is_blocked_ip
# categories (private, loopback, link-local incl. 169.254.169.254 metadata, CGNAT,
# multicast, reserved, unspecified, broadcast). Kept explicit (NOT shared with
# ssrf_guard) so a parity test pins the two without either regressing.
_V4_DROP = (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "127.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "198.18.0.0/15",
    "224.0.0.0/4", "240.0.0.0/4", "0.0.0.0/8", "255.255.255.255/32",
)
# IPv6 destinations (constant, emitted unconditionally: the netns always has ::1 and a
# fe80:: on the veth). ::ffff:0.0.0.0/96 is intentionally omitted -- v4-mapped
# destinations egress as real IPv4 packets caught by _V4_DROP.
_V6_DROP = ("::1/128", "::/128", "fc00::/7", "fe80::/10", "ff00::/8", "64:ff9b::/96")

_METADATA = ("169.254.169.254", 80)


def build_egress_ruleset(own_v4_cidrs, own_v6_cidrs=()):
    """Return the nftables ruleset string. Pure. Emits own-subnet accepts ONE rule per
    element (never an anonymous `{}` set -- an empty set is a parse error -> fail-closed).
    Raises ValueError on empty own_v4_cidrs: the own-subnet accept is the only rule
    keeping redis + the DNS resolver reachable, so an empty list must fail loud."""
    own_v4 = [str(c) for c in own_v4_cidrs]
    own_v6 = [str(c) for c in own_v6_cidrs]
    if not own_v4:
        raise ValueError("build_egress_ruleset: own_v4_cidrs is empty")
    lines = [
        f"table inet {TABLE} {{",
        "  chain output {",
        "    type filter hook output priority 0; policy accept;",
        '    oif "lo" accept',
    ]
    lines += [f"    ip daddr {c} accept" for c in own_v4]
    lines += [f"    ip6 daddr {c} accept" for c in own_v6]
    lines.append("    ip daddr { %s } drop" % ", ".join(_V4_DROP))
    lines.append("    ip6 daddr { %s } drop" % ", ".join(_V6_DROP))
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def _valid_own(cidrs):
    """Keep only CIDRs that are a private IPv4 container subnet nested in a broad
    RFC1918 range (so a special/metadata range can never be accepted)."""
    good = []
    for c in cidrs:
        try:
            net = ipaddress.ip_network(c, strict=False)
        except ValueError:
            continue
        if net.version == 4 and net.is_private and any(net.subnet_of(b) for b in _BROAD_RFC1918):
            good.append(net)
    return good


def _ip_addr_cidrs(family):
    """Global-scope CIDRs from `ip -o <family> addr show scope global`."""
    out = subprocess.run(
        ["ip", "-o", family, "addr", "show", "scope", "global"],
        capture_output=True, text=True, check=True,
    ).stdout
    cidrs = []
    for line in out.splitlines():
        parts = line.split()
        for key in ("inet", "inet6"):
            if key in parts:
                cidrs.append(parts[parts.index(key) + 1])
    return cidrs


def discover_own_v4():
    override = os.getenv("EGRESS_OWN_SUBNET")
    if override:
        return _valid_own([override])
    return _valid_own(_ip_addr_cidrs("-4"))


def discover_own_v6():
    """Global-scope IPv6 subnets the container owns (empty on a v4-only network like
    fingpt-net). Accepted as-is so same-net v6 peers stay reachable; the v6 drop block
    still covers every OTHER v6 range."""
    good = []
    for c in _ip_addr_cidrs("-6"):
        try:
            net = ipaddress.ip_network(c, strict=False)
        except ValueError:
            continue
        if net.version == 6:
            good.append(net)
    return good


def _redis_host_port():
    p = urlparse(os.getenv("REDIS_URL", "redis://fingpt-redis:6379/0"))
    return (p.hostname or "fingpt-redis", p.port or 6379)


def _tcp_ok(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _self_test():
    """Prove the firewall is actually enforcing before the app serves. A syntactically
    valid but wrong ruleset loads fine (nft exit 0) and passes the static /health/ gate,
    so nft's exit code alone is NOT fail-closed -- this active check is."""
    if _tcp_ok(*_METADATA):
        sys.stderr.write("FATAL self-test: 169.254.169.254 reachable; egress firewall not enforcing\n")
        return 1
    host, port = _redis_host_port()
    try:
        socket.gethostbyname(host)
    except OSError:
        sys.stderr.write(f"FATAL self-test: cannot resolve {host!r}; DNS egress blocked\n")
        return 1
    for _ in range(6):  # tolerate redis cold start (~5s window)
        if _tcp_ok(host, port):
            break
        time.sleep(1)
    else:
        sys.stderr.write(f"FATAL self-test: {host}:{port} unreachable; own-subnet accept broken\n")
        return 1
    return 0


def _emit():
    v4 = discover_own_v4()
    if not v4:
        sys.stderr.write("FATAL: egress firewall could not determine a valid own IPv4 subnet\n")
        return 3
    sys.stdout.write(build_egress_ruleset(v4, discover_own_v6()))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        return _self_test()
    return _emit()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd Main/backend && .venv/bin/pytest tests/test_egress_firewall.py -q`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add Main/backend/ops/__init__.py Main/backend/ops/egress_firewall.py Main/backend/tests/test_egress_firewall.py
git commit -m "$(cat <<'EOF'
feat(security): egress firewall ruleset generator + tests

Pure build_egress_ruleset + discovery/validation + --self-test for the WS/WebRTC
SSRF egress firewall. Hermetic unit tests incl. parity vs ssrf_guard._is_blocked_ip.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Dockerfile — install tooling, run as root-in-userns

**Files:**
- Modify: `Main/backend/Dockerfile`

- [ ] **Step 1: Add nftables/iproute2/util-linux to the apt install**

In the second `apt-get install -y` list (the one installing `python3.12`, `nodejs`), add `nftables`, `iproute2`, `util-linux` (the block ends with `&& apt-get clean && rm -rf /var/lib/apt/lists/*`). Result:

```dockerfile
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    nodejs \
    nftables \
    iproute2 \
    util-linux \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Remove `USER fingpt` and update the comment**

Replace the comment block above `RUN groupadd ...` + the `USER fingpt` line so the image keeps creating `fingpt` and owning the writable dirs, but PID1 stays root-in-userns (the entrypoint drops to `fingpt`). Change:

```dockerfile
# Create a non-root runtime user and own ONLY the writable runtime dirs.
# ... (existing comment) ...
RUN groupadd --system --gid 1001 fingpt \
    && useradd --system --uid 1001 --gid fingpt --no-create-home fingpt \
    && chown -R fingpt:fingpt /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/runtime

USER fingpt

ENTRYPOINT ["/app/entrypoint.sh"]
```

to:

```dockerfile
# Create the non-root runtime user and own ONLY the writable runtime dirs. The
# application source under /app stays root-owned so a compromised process cannot
# rewrite code at runtime (P0 Root A.3: no-write-under-/app). PID1 (entrypoint.sh)
# runs as root-IN-USERNS -- rootless podman keeps that host-unprivileged -- solely to
# load the SSRF egress firewall (needs CAP_NET_ADMIN) and chown the runtime mount,
# then setpriv-drops to fingpt (uid1001, NET_ADMIN removed from the bounding set,
# no_new_privs) so the long-running app is non-root and cannot flush its own firewall.
# The no-write-under-/app intent holds: the app process is uid1001 and /app is
# root-owned; only the brief pre-app init is root and it writes nothing under /app
# except chowning the designated writable runtime mount.
RUN groupadd --system --gid 1001 fingpt \
    && useradd --system --uid 1001 --gid fingpt --no-create-home fingpt \
    && chown -R fingpt:fingpt /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/runtime

ENTRYPOINT ["/app/entrypoint.sh"]
```

(No setcap anywhere. The `HEALTHCHECK`/`CMD` lines below are unchanged.)

- [ ] **Step 3: Verify (syntax-level; full build happens in Task 8 on the droplet)**

Run: `grep -n 'USER fingpt' Main/backend/Dockerfile` → Expected: no output (line removed).
Run: `grep -n 'nftables\|iproute2\|util-linux' Main/backend/Dockerfile` → Expected: the three packages present.

- [ ] **Step 4: Commit**

```bash
git add Main/backend/Dockerfile
git commit -m "$(cat <<'EOF'
feat(security): image tooling + root-in-userns init for egress firewall

Install nftables/iproute2/util-linux; drop USER fingpt so PID1 loads the firewall
as root-in-userns then setpriv-drops to uid1001. No setcap.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: entrypoint.sh — root-init-drop

**Files:**
- Modify: `Main/backend/entrypoint.sh`

- [ ] **Step 1: Insert the root init phase at the top (after the shebang + `set -eu`)**

Immediately after line 2 (`set -eu`) and BEFORE the `REQUIRE_OPENAI_API_KEY=...` line, insert:

```sh

# ---- root init phase: load + PROVE the SSRF egress firewall, then drop privilege ----
# Runs only on the first (root-in-userns) invocation; re-execs self as uid1001.
# See ops/egress_firewall.py and Docs/superpowers/specs/2026-07-02-*egress-firewall*.
if [ "$(id -u)" = "0" ]; then
    RULES="$(mktemp)"
    # Temp-file, NOT a pipe: `python ... | nft -f -` fails OPEN under dash (a generator
    # crash yields empty stdin, nft exits 0, set -e never fires -> we serve with NO
    # firewall). Each guard below is fail-closed-and-loud, matching this file's idiom.
    python -m ops.egress_firewall > "$RULES" \
        || { echo "FATAL: egress ruleset generation failed" >&2; exit 1; }
    [ -s "$RULES" ] \
        || { echo "FATAL: egress ruleset empty" >&2; exit 1; }
    grep -q '169.254.0.0/16' "$RULES" \
        || { echo "FATAL: egress ruleset missing metadata drop sentinel" >&2; exit 1; }
    grep -qE 'ip6? daddr [0-9a-fA-F].* accept' "$RULES" \
        || { echo "FATAL: egress ruleset missing own-subnet accept" >&2; exit 1; }
    nft -f "$RULES" \
        || { echo "FATAL: nft failed to load egress ruleset" >&2; exit 1; }
    rm -f "$RULES"
    nft list table inet ssrf_egress >/dev/null 2>&1 \
        || { echo "FATAL: ssrf_egress table absent after load" >&2; exit 1; }
    # Active proof the DROP bites AND the own-subnet accept did not strand redis/DNS.
    python -m ops.egress_firewall --self-test \
        || { echo "FATAL: egress firewall self-test failed" >&2; exit 1; }
    echo "SSRF egress firewall loaded and self-tested."
    # :U chowned the runtime mount to root (PID1 is root); hand it to the app user.
    chown -R fingpt:fingpt /app/runtime
    # Drop to uid1001, remove NET_ADMIN from the BOUNDING set (so a compromised app
    # cannot re-arm/flush the firewall), set no_new_privs, and re-exec self as fingpt.
    exec setpriv --reuid=1001 --regid=1001 --init-groups \
         --bounding-set=-net_admin --no-new-privs -- "$0" "$@"
fi
# ---- app phase (uid 1001) continues below, unchanged ----
```

The remainder of the file (the `REQUIRE_OPENAI_API_KEY` gate, cache dir, truthlayer build, collectstatic, Playwright verify, `exec "$@"`) is unchanged; it now runs only in the dropped app phase.

- [ ] **Step 2: Verify shell syntax**

Run: `sh -n Main/backend/entrypoint.sh`
Expected: no output (valid). Also `grep -c 'exec ' Main/backend/entrypoint.sh` → Expected: `2` (the setpriv re-exec + the final `exec "$@"`).

- [ ] **Step 3: Commit**

```bash
git add Main/backend/entrypoint.sh
git commit -m "$(cat <<'EOF'
feat(security): root-init-drop entrypoint loads the egress firewall fail-closed

Guarded temp-file nft load + post-load table assertion + active self-test (metadata
unreachable, redis/DNS reachable), chown runtime, then setpriv-drop to uid1001 with
NET_ADMIN removed from the bounding set + no_new_privs.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Deploy workflow (--cap-add + pre-cutover gate) + compose cap_add

**Files:**
- Modify: `.github/workflows/backend-deploy.yml`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add `--cap-add=NET_ADMIN` to the systemd override podman run**

In `backend-deploy.yml`, the override `ExecStart` line (currently begins `ExecStart=/usr/bin/podman run --name ${SYSTEMD_UNIT} --replace --rm ...`) — add `--cap-add=NET_ADMIN` right after `--replace --rm` and add a comment above the `cat > "$OVERRIDE_DIR/override.conf"` heredoc:

```bash
            # --cap-add=NET_ADMIN is SECURITY/FUNCTIONALLY LOAD-BEARING: rootless podman's
            # default cap set excludes NET_ADMIN, so without it the root-in-userns init
            # cannot load the SSRF egress firewall (nft EPERM) -> entrypoint fail-closes ->
            # the container never serves. Do not drop it. (The app itself runs as uid1001
            # with NET_ADMIN removed from its bounding set; see entrypoint.sh.)
```

Resulting `ExecStart` (single line): `...--replace --rm --cap-add=NET_ADMIN --cgroups=split ...` (insert only `--cap-add=NET_ADMIN`; leave everything else, including `-v /home/deploy/fingpt/runtime:/app/runtime:U,Z`, unchanged).

- [ ] **Step 2: Add the pre-cutover validation gate**

In the "Deploy to Fedora droplet" step's `script:`, AFTER `podman pull "$REMOTE_IMAGE"` and BEFORE the `podman network exists fingpt-net ...` / systemd-restart section, insert:

```bash
            # Pre-cutover firewall validation: prove the NEW image can generate + load
            # the egress ruleset and pass its self-test on fingpt-net BEFORE the
            # systemctl restart's --replace kills the currently-serving container. On
            # failure, abort so the OLD container keeps serving (there is no auto-rollback).
            echo "Validating egress firewall on the new image (pre-cutover)..."
            podman run --rm --cap-add=NET_ADMIN --network fingpt-net \
              --entrypoint sh "$REMOTE_IMAGE" -c '
                set -eu
                RULES=$(mktemp)
                python -m ops.egress_firewall > "$RULES"
                nft -f "$RULES"
                nft list table inet ssrf_egress >/dev/null
                python -m ops.egress_firewall --self-test
              ' || { echo "ERROR: egress firewall pre-cutover validation FAILED; aborting deploy (old container left serving)"; exit 1; }
            echo "Egress firewall pre-cutover validation passed."

            # Record the currently-deployed image for manual rollback (no auto-rollback).
            PREV_IMAGE=$(podman inspect --format '{{.ImageName}}' "$SYSTEMD_UNIT" 2>/dev/null || echo "unknown")
            echo "Previous image (manual rollback target): $PREV_IMAGE"
```

(The pre-cutover container needs `fingpt-net` to exist; the existing `podman network exists fingpt-net || podman network create fingpt-net` line runs just after — move the network-ensure line to BEFORE this validation block so the network is present. Concretely: relocate `podman network exists fingpt-net || podman network create fingpt-net` to immediately after `podman pull "$REMOTE_IMAGE"`, then the validation block, then the redis `podman run` and the override.)

- [ ] **Step 3: Add cap_add to docker-compose**

In `docker-compose.yml`, under the `api` service (same indentation level as its `image:`/`build:`/`ports:` keys), add:

```yaml
    cap_add:
      - NET_ADMIN   # required: entrypoint loads the SSRF egress firewall (nft) at startup
```

- [ ] **Step 4: Verify YAML**

Run: `Main/backend/.venv/bin/python -c "import yaml,sys; [yaml.safe_load(open(p)) for p in ['.github/workflows/backend-deploy.yml','docker-compose.yml']]; print('yaml ok')"`
Expected: `yaml ok`.
Run: `grep -n 'cap-add=NET_ADMIN\|cap_add\|pre-cutover' .github/workflows/backend-deploy.yml docker-compose.yml` → Expected: the additions present.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/backend-deploy.yml docker-compose.yml
git commit -m "$(cat <<'EOF'
ci(security): --cap-add=NET_ADMIN + pre-cutover firewall validation; compose cap_add

Grant NET_ADMIN (load-bearing) to the api container; validate the new image can load
the egress firewall on fingpt-net before the --replace cutover (abort on failure so
the old container keeps serving); grant NET_ADMIN in docker-compose for local dev.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Chromium WebRTC/QUIC hardening (defense-in-depth)

**Files:**
- Modify: `Main/backend/datascraper/playwright_tools.py:37-40`
- Modify: `Main/backend/datascraper/url_tools.py:205`
- Test: `Main/backend/tests/test_ssrf_wire.py`

- [ ] **Step 1: Write the failing wiring tests**

Append to `Main/backend/tests/test_ssrf_wire.py` (imports `AsyncMock, MagicMock, patch` already present):

```python
class ChromiumEgressHardeningTests(SimpleTestCase):
    """WebRTC/QUIC are disabled at the Chromium layer (defense-in-depth) in BOTH the
    async factory and the sync fallback: --disable-quic on the launch args, and an
    init script that removes RTCPeerConnection so page JS cannot open WebRTC at all."""

    def test_async_factory_disables_webrtc_and_quic(self):
        import datascraper.playwright_tools as pt

        page = MagicMock(name="page")
        page.add_init_script = AsyncMock()
        context = MagicMock(name="context")
        context.new_page = AsyncMock(return_value=page)
        browser = MagicMock(name="browser")
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        launch_kwargs = {}

        async def _launch(**kwargs):
            launch_kwargs.update(kwargs)
            return browser

        playwright = MagicMock(name="playwright")
        playwright.chromium.launch = AsyncMock(side_effect=_launch)
        playwright.stop = AsyncMock()
        ap = MagicMock()
        ap.start = AsyncMock(return_value=playwright)

        async def run():
            with patch("playwright.async_api.async_playwright", return_value=ap), \
                 patch("datascraper.ssrf_guard.install_route_guard", new=AsyncMock()):
                async with pt.PlaywrightBrowser():
                    pass

        asyncio.run(run())
        args = launch_kwargs.get("args", [])
        assert "--disable-quic" in args
        page.add_init_script.assert_awaited()
        script = page.add_init_script.await_args.args[0]
        assert "RTCPeerConnection" in script

    def test_sync_path_disables_webrtc_and_quic(self):
        import datascraper.url_tools as ut
        src = (ut.__file__)
        text = open(src, "r", encoding="utf-8").read()
        # sync path launch args include --disable-quic and it installs a WebRTC-removing init script
        assert "--disable-quic" in text
        assert "add_init_script" in text and "RTCPeerConnection" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Main/backend && .venv/bin/pytest tests/test_ssrf_wire.py::ChromiumEgressHardeningTests -q`
Expected: FAIL (`--disable-quic` not in args; `add_init_script` not awaited).

- [ ] **Step 3: Define the shared WebRTC-removal init script + apply in the async factory**

In `playwright_tools.py`, add a module constant near the top (after `logger = ...`):

```python
# Defense-in-depth for the SSRF egress firewall: a text scraper needs neither WebRTC
# nor QUIC. Removing the RTCPeerConnection constructors in every frame prevents page
# JS from opening WebRTC (ICE/STUN over UDP) at all; --disable-quic (launch arg) drops
# QUIC/HTTP3. The netns egress firewall remains the actual boundary; this only shrinks
# the surface for the documented public-egress residual.
_DISABLE_WEBRTC_JS = (
    "delete window.RTCPeerConnection;"
    "delete window.webkitRTCPeerConnection;"
    "delete window.RTCDataChannel;"
)
```

Add `--disable-quic` to the launch args, and install the init script right where the route guard is installed:

```python
        browser = await playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
                  '--disable-quic']
        )
```

and, in the factory body just before/after `await ssrf_guard.install_route_guard(page)`:

```python
        page = await context.new_page()
        # Every page is born guarded (SSRF route guard) and WebRTC-hardened.
        await ssrf_guard.install_route_guard(page)
        await page.add_init_script(_DISABLE_WEBRTC_JS)

        yield page
```

- [ ] **Step 4: Apply the same in the sync path**

In `url_tools.py`, add the same constant near the top of the module (after imports):

```python
_DISABLE_WEBRTC_JS = (
    "delete window.RTCPeerConnection;"
    "delete window.webkitRTCPeerConnection;"
    "delete window.RTCDataChannel;"
)
```

Change the sync launch (L205) to include `--disable-quic`:

```python
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-quic'])
```

and after `ssrf_guard.install_route_guard_sync(page)` add:

```python
                ssrf_guard.install_route_guard_sync(page)
                page.add_init_script(_DISABLE_WEBRTC_JS)
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd Main/backend && .venv/bin/pytest tests/test_ssrf_wire.py -q`
Expected: PASS (the new class + the existing wire tests).

- [ ] **Step 6: Commit**

```bash
git add Main/backend/datascraper/playwright_tools.py Main/backend/datascraper/url_tools.py Main/backend/tests/test_ssrf_wire.py
git commit -m "$(cat <<'EOF'
feat(security): disable WebRTC + QUIC at Chromium (egress firewall defense-in-depth)

Remove RTCPeerConnection in every frame (both async factory + sync path) and add
--disable-quic, shrinking the public WS/WebRTC/QUIC exfil surface the netns firewall
leaves as a documented residual.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Off-box uptime monitor

**Files:**
- Create: `.github/workflows/uptime.yml`

- [ ] **Step 1: Create the workflow**

```yaml
name: Uptime Monitor

# Deploy-independent liveness: the backend fail-closes on a bad egress-firewall load,
# and a load can fail on a reboot/OOM/kernel update WITHOUT a deploy (which re-runs the
# entrypoint). This cron curls the PUBLIC endpoint so such an outage is caught within
# minutes instead of by a user. Zero-infra fallback; GitHub may delay scheduled runs and
# disables schedules after 60 days of repo inactivity.
on:
  schedule:
    - cron: "*/10 * * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - name: Curl public health endpoint
        run: |
          set -euo pipefail
          for i in 1 2 3; do
            if curl -fsS -m 15 https://agenticfinsearch.org/health/ >/dev/null; then
              echo "healthy (attempt $i)"
              exit 0
            fi
            echo "attempt $i failed; retrying in 20s..."
            sleep 20
          done
          echo "::error::agenticfinsearch.org/health/ is DOWN after 3 attempts"
          exit 1
```

- [ ] **Step 2: Verify YAML**

Run: `Main/backend/.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/uptime.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/uptime.yml
git commit -m "$(cat <<'EOF'
feat(ops): off-box uptime monitor for deploy-independent fail-closed detection

Scheduled curl of the public /health/ so a reboot/OOM/update that fails the egress
firewall load (fail-closed, no auto-rollback) is caught within minutes.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Full verification + droplet validation of the built branch image

**Files:** none (verification only)

- [ ] **Step 1: Full backend test suite**

Run: `cd Main/backend && .venv/bin/pytest tests -q`
Expected: PASS with no regressions (PR #325 baseline was 554 passed, 1 skipped; +new egress/hardening tests).

- [ ] **Step 2: Django system checks + deployment readiness (mirrors CI build job)**

Run: `cd Main/backend && .venv/bin/python manage.py check` then `.venv/bin/python verify_deployment.py`
Expected: both succeed.

- [ ] **Step 3: Build the branch image on the droplet and run the full production-shape validation**

Push the branch, then on the droplet build from the branch checkout and run the exact pre-cutover validation + reachability matrix as a throwaway container (never touching fingpt-api/redis). Use the probe pattern already validated in the spec's Appendix A: build the image, `podman run --rm --cap-add=NET_ADMIN --network fingpt-net --entrypoint sh <img> -c '<generate+load+list+self-test>'`, and confirm metadata is UNREACHABLE while redis/DNS/public are OK; then delete the throwaway image.

Expected: `nft` loads, self-test passes, metadata blocked, redis/DNS/public reachable.

- [ ] **Step 4: Push the branch**

```bash
git push -u origin security/ws-webrtc-egress-firewall
```

---

## Task 8: Self-review, open PR, PAUSE for user review

- [ ] **Step 1: Run `/code-review` (or an adversarial review workflow) against the branch diff; fix every finding in-branch.**
- [ ] **Step 2: Re-run the full suite after fixes (`.venv/bin/pytest tests -q`).**
- [ ] **Step 3: Open the PR** (title ≤50 chars; body links the spec + this plan; lists the documented residuals + the tracked ssrf_guard CGNAT follow-up; notes the pre-cutover gate + manual-rollback).
- [ ] **Step 4: PAUSE — do NOT merge.** Present the PR + spec for user review (merging `main` auto-deploys to prod with no rollback). Merge only on explicit user approval.

---

## Tracked follow-ups (NOT in this PR)

- **ssrf_guard CGNAT gap:** `100.64.0.0/10` is dropped by the firewall but NOT blocked by `ssrf_guard._is_blocked_ip` (`is_private=False` on Python 3.12/3.13). Add it to the HTTP-side guard in a separate PR with its own test.
- **redis AUTH / protected-mode** and **Chromium-in-separate-netns** — the only real closes for the browser→redis/DNS residual; separate designs.

## Self-Review (plan vs spec)

- Spec §"root-init-drop" → Task 3. §ruleset → Task 1 (`build_egress_ruleset`, exact CIDRs, per-element accepts, no `ct`). §generator/validation/self-test → Tasks 1. §Dockerfile → Task 2. §deploy workflow (cap + pre-cutover gate + rollback note) → Task 4. §compose → Task 4. §Chromium hardening → Task 5. §uptime monitor → Task 6. §testing (unit, parity, subprocess) → Task 1; droplet load → Task 7. §residuals + CGNAT follow-up → Tracked follow-ups + PR body.
- Type/name consistency: `build_egress_ruleset`, `_valid_own`, `_V4_DROP`, `_V6_DROP`, `discover_own_v4`, `TABLE="ssrf_egress"`, `--self-test`, `EGRESS_OWN_SUBNET`, `_DISABLE_WEBRTC_JS` used identically across tasks and tests.
- Placeholder scan: every code/step is concrete; the only intentional runtime-verified item (exact Chromium flag efficacy) is covered by the reliable init-script removal of `RTCPeerConnection`, which is asserted by test.
