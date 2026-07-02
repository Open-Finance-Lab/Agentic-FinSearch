"""Container network-namespace egress firewall for the SSRF boundary.

Chromium (Playwright) runs in-process and egresses WebSocket/WebRTC/QUIC on its own
socket, which page.route (and thus ssrf_guard's HTTP route guard) cannot intercept.
This module generates an nftables ``inet`` ruleset -- loaded into the container's own
netns at startup by entrypoint.sh, as root, before dropping to uid1001 -- that DROPs
egress to every private / link-local / metadata / special range for ALL protocols and
ports, while ACCEPTing the container's own subnet (redis + the podman DNS resolver +
gateway) and public destinations. One boundary, all protocols.

``build_egress_ruleset`` is pure + stdlib-only so the security-critical ruleset is
hermetically unit-testable. Discovery + validation live in ``main()``; the active
runtime self-test lives behind ``--self-test``.
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
# into a special/metadata range can never become an ``accept``.
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

# A discovered own-v6 subnet must be a real ULA (fc00::/7) or GUA (2000::/3) the container
# owns -- symmetric with _valid_own's v4 nesting check. (Cross-version subnet_of raises, so
# v6 cannot reuse the v4 broad-RFC1918 list.)
_BROAD_V6 = (ipaddress.ip_network("fc00::/7"), ipaddress.ip_network("2000::/3"))

_METADATA = ("169.254.169.254", 80)


def build_egress_ruleset(own_v4_cidrs, own_v6_cidrs=()):
    """Return the nftables ruleset string. Pure. Emits own-subnet accepts ONE rule per
    element (never an anonymous ``{}`` set -- an empty set is a parse error ->
    fail-closed). Raises ValueError on empty ``own_v4_cidrs``: the own-subnet accept is
    the only rule keeping redis + the DNS resolver reachable, so an empty list must fail
    loud rather than silently strand internal infra."""
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
    """Global-scope CIDRs from ``ip -o <family> addr show scope global``."""
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
    fingpt-net). Constrained to real ULA/GUA the container owns (symmetric with the v4
    nesting check) so same-net v6 peers stay reachable; the v6 drop block still covers
    every OTHER v6 range."""
    good = []
    for c in _ip_addr_cidrs("-6"):
        try:
            net = ipaddress.ip_network(c, strict=False)
        except ValueError:
            continue
        if net.version == 6 and any(net.subnet_of(b) for b in _BROAD_V6):
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
    """Prove the firewall's DROP is enforcing before the app serves.

    SECURITY-CRITICAL leg (fatal): the metadata IP must be UNREACHABLE after load. This
    is a sound "the drop bites" proof only where the target is otherwise routable (the
    prod droplet + the deploy pre-cutover gate). The caller passes METADATA_WAS_REACHABLE
    so an unreachable result is treated as a true reachable->blocked transition on cloud,
    and flagged INCONCLUSIVE (warn, not proof) off-cloud (e.g. `docker compose up`), where
    169.254.169.254 is not served.

    AVAILABILITY legs (WARN, non-fatal): DNS + redis reachability. The prior entrypoint
    had NO redis/DNS boot dependency (redis is a request-time soft dependency), so making
    them fatal would regress resilience -- a redis blip at reboot could fail-close a
    correct firewall. The own-subnet accept's PRESENCE is already guarded upstream (main()
    fails loud on empty discovery; entrypoint greps for the accept line), so these legs
    are observability, not a boot gate."""
    if _tcp_ok(*_METADATA):
        sys.stderr.write("FATAL self-test: 169.254.169.254 reachable; egress firewall not enforcing\n")
        return 1
    if os.getenv("METADATA_WAS_REACHABLE") == "0":
        sys.stderr.write(
            "WARN self-test: metadata target was not routable pre-load; the drop-bites "
            "proof is INCONCLUSIVE on this host (expected off-cloud, e.g. dev compose)\n"
        )
    host, port = _redis_host_port()
    try:
        socket.gethostbyname(host)
        dns_ok = True
    except OSError:
        dns_ok = False
    redis_ok = False
    if dns_ok:
        for _ in range(6):  # tolerate redis cold start (~5s window)
            if _tcp_ok(host, port):
                redis_ok = True
                break
            time.sleep(1)
    if not dns_ok:
        sys.stderr.write(f"WARN self-test: cannot resolve {host!r} (DNS/redis may be down); continuing\n")
    elif not redis_ok:
        sys.stderr.write(f"WARN self-test: {host}:{port} unreachable (redis may be down); continuing\n")
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
    if "--metadata-reachable" in argv:
        # For the caller's before/after transition proof: exit 0 iff the metadata IP is
        # reachable right now (run this BEFORE loading the ruleset).
        return 0 if _tcp_ok(*_METADATA) else 1
    if "--self-test" in argv:
        return _self_test()
    return _emit()


if __name__ == "__main__":
    sys.exit(main())
