"""Egress firewall ruleset-generator tests (WS/WebRTC SSRF).

Pure/hermetic: build_egress_ruleset takes CIDRs and returns a string; no network,
no nft, no container. The one subprocess test uses EGRESS_OWN_SUBNET so it does not
depend on the runner's interfaces.
"""
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ops.egress_firewall as ef
from ops.egress_firewall import (
    _V4_DROP,
    _V6_DROP,
    _valid_own,
    build_egress_ruleset,
    discover_own_v4,
)
from datascraper import ssrf_guard

_BACKEND = Path(__file__).resolve().parent.parent


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
    before_drop = build_egress_ruleset(["10.89.0.0/24"], []).split("ip6 daddr {")[0]
    assert "ip6 daddr" not in before_drop
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


def test_override_env_suppresses_v6_discovery(monkeypatch):
    # The override is the COMPLETE own-subnet spec: v6 discovery must not shell out
    # either, keeping the override path hermetic on hosts without iproute2.
    monkeypatch.setenv("EGRESS_OWN_SUBNET", "10.89.0.0/24")
    monkeypatch.setattr(
        ef.subprocess, "run",
        lambda *a, **k: pytest.fail("override must suppress the ip subprocess"),
    )
    assert ef.discover_own_v6() == []


# Parity vs ssrf_guard: the two "what is private" definitions must not silently diverge.
# CPython's address properties miss two kinds of firewall-dropped space: all of
# 100.64/10 (CGNAT: neither private nor global to IANA) and the two globally-reachable
# anycast carve-outs inside 192.0.0.0/24 (192.0.0.9 PCP, 192.0.0.10 TURN, per the
# post-CVE-2024-4032 registry alignment); ssrf_guard blocks both ranges explicitly
# (_EXTRA_BLOCKED_NETS).
_DANGEROUS = [
    "169.254.169.254", "10.1.2.3", "172.16.5.5", "192.168.1.1", "127.0.0.1",
    "0.0.0.0", "255.255.255.255", "198.18.0.1", "192.0.0.170", "240.0.0.1",
    "100.64.0.1", "192.0.0.9", "192.0.0.10", "::1", "fc00::1", "fe80::1",
]


def test_parity_with_ssrf_guard_block_list():
    rs = build_egress_ruleset(["10.89.0.0/24"])
    for ip in _DANGEROUS:
        assert ssrf_guard._is_blocked_ip(ip), f"ssrf_guard should block {ip}"
        addr = ipaddress.ip_address(ip)
        drops = _V4_DROP if addr.version == 4 else _V6_DROP
        assert any(addr in ipaddress.ip_network(c) for c in drops), f"firewall should drop {ip}"


def test_every_firewall_drop_range_blocked_http_side():
    # Structural parity, the reverse direction of the sample list above: EVERY
    # range the netns firewall drops must also be blocked by ssrf_guard in-process,
    # so a range added to _V4_DROP/_V6_DROP can never silently stay fetchable at
    # the HTTP layer. Ranges up to /15 are enumerated EXHAUSTIVELY (~1.4s total):
    # IANA carve-outs are single addresses inside small special-purpose blocks
    # (192.0.0.9/.10 hid from first/middle/last sampling exactly this way), so
    # sampling a small range proves nothing. The /15 threshold covers every
    # special-purpose block in the drop lists (198.18.0.0/15 is the largest);
    # what remains -- RFC1918 /12 and /8s, the /4s, the huge v6 blocks -- gets
    # first/middle/last: no registry carve-outs exist there, and enumeration
    # genuinely is infeasible (the /12 alone costs ~6s, a /8 ~90s).
    for cidr in _V4_DROP + _V6_DROP:
        net = ipaddress.ip_network(cidr)
        if net.num_addresses <= 131072:
            addrs = iter(net)
        else:
            addrs = iter({net.network_address, net.broadcast_address,
                          net.network_address + net.num_addresses // 2})
        for addr in addrs:
            assert ssrf_guard._is_blocked_ip(str(addr)), (
                f"{addr} is in firewall-dropped {cidr} but ssrf_guard does not block it"
            )


def test_subprocess_invocation_is_hermetic():
    env = dict(os.environ, EGRESS_OWN_SUBNET="10.89.0.0/24")
    r = subprocess.run(
        [sys.executable, "-m", "ops.egress_firewall"],
        cwd=_BACKEND, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "table inet ssrf_egress" in r.stdout
    assert "169.254.0.0/16" in r.stdout


# --- discovery parser (security-load-bearing; the override tests above bypass it) ---

def _patch_ip(monkeypatch, stdout):
    monkeypatch.setattr(ef.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout))
    monkeypatch.delenv("EGRESS_OWN_SUBNET", raising=False)


def test_discover_own_v4_parses_real_ip_output(monkeypatch):
    # brd token, secondary addr on a second interface — the parser must pick the CIDR
    # right after each 'inet', not the brd address or the ifname column.
    sample = (
        "2: eth0    inet 10.89.0.26/24 brd 10.89.0.255 scope global eth0\\       valid_lft forever\n"
        "3: eth1    inet 172.20.0.5/16 brd 172.20.255.255 scope global eth1\\       valid_lft forever\n"
    )
    _patch_ip(monkeypatch, sample)
    assert [str(n) for n in discover_own_v4()] == ["10.89.0.0/24", "172.20.0.0/16"]


def test_discover_own_v4_rejects_rogue_special_scope_global(monkeypatch):
    # A rogue link-local/metadata route showing as scope global must NOT become an accept.
    sample = (
        "2: eth0 inet 10.89.0.26/24 brd 10.89.0.255 scope global eth0\n"
        "3: eth1 inet 169.254.10.5/16 scope global eth1\n"
    )
    _patch_ip(monkeypatch, sample)
    assert [str(n) for n in discover_own_v4()] == ["10.89.0.0/24"]


def test_self_test_metadata_reachable_is_fatal(monkeypatch):
    monkeypatch.setattr(ef, "_tcp_ok", lambda h, p, timeout=3: h == "169.254.169.254")
    assert ef._self_test() == 1


def test_self_test_redis_and_dns_down_is_nonfatal(monkeypatch):
    # metadata blocked (good) but redis/DNS down -> WARN + continue (0), matching the
    # prior entrypoint's zero redis-boot-dependency (no resilience regression).
    monkeypatch.setattr(ef, "_tcp_ok", lambda h, p, timeout=3: False)

    def _boom(_):
        raise OSError("no dns")

    monkeypatch.setattr(ef.socket, "gethostbyname", _boom)
    assert ef._self_test() == 0


def test_self_test_passes_when_blocked_and_infra_up(monkeypatch):
    monkeypatch.setattr(ef, "_tcp_ok", lambda h, p, timeout=3: h != "169.254.169.254")
    monkeypatch.setattr(ef.socket, "gethostbyname", lambda h: "10.89.0.5")
    assert ef._self_test() == 0


def test_entrypoint_grep_sentinels_match_generated_ruleset():
    # The entrypoint's own-subnet-accept grep pattern must match what the generator emits.
    entry = (_BACKEND / "entrypoint.sh").read_text()
    rs = build_egress_ruleset(["10.89.0.0/24"])
    assert "169.254.0.0/16" in entry and "169.254.0.0/16" in rs
    m = re.search(r"grep -qE '([^']+)'", entry)
    assert m, "own-subnet accept 'grep -qE' pattern not found in entrypoint.sh"
    assert re.search(m.group(1), rs), f"entrypoint grep {m.group(1)!r} does not match ruleset"
    # The entrypoint's post-load table check must use the SAME table name the generator
    # creates (the literal is spelled in both places; pin them together).
    assert f"nft list table inet {ef.TABLE}" in entry
