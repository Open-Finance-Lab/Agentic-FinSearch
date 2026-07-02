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
