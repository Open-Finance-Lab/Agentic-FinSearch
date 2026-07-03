"""Static guards for the edge security-header block (Root G hygiene).

Runs in the standard backend harness (SimpleTestCase, no DB):
    uv run pytest tests/test_caddyfile_headers.py -q

These pin Deploy/podman/Caddyfile.example so a later edit cannot silently
drop `defer` (without which Django's SecurityMiddleware copies duplicate
HSTS and out-win Referrer-Policy -- headers "configured" but never
delivered), weaken the API-only CSP, or lose the P0 Root C.1 forwarding
override. The LIVE edge is verified separately by uptime.yml's header probe;
this suite keeps the repo's canonical example from drifting underneath it.
"""
import os

from django.test import SimpleTestCase

_HERE = os.path.dirname(os.path.abspath(__file__))
CADDYFILE = os.path.join(
    _HERE, "..", "..", "..", "Deploy", "podman", "Caddyfile.example"
)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class CaddyfileHeaderBlockTests(SimpleTestCase):
    def setUp(self):
        self.text = _read(CADDYFILE)

    def _header_block(self):
        # Extract the site-level `header { ... }` block specifically -- a
        # whole-file assertIn would pass with the directives moved into a
        # comment or into some never-matched handler.
        lines = self.text.splitlines()
        starts = [i for i, l in enumerate(lines) if l.strip() == "header {"]
        self.assertEqual(
            len(starts), 1, f"expected exactly one `header {{` block, got {len(starts)}"
        )
        start = starts[0]
        end = next(
            i for i in range(start + 1, len(lines)) if lines[i].strip() == "}"
        )
        return lines[start + 1:end]

    def _ops(self):
        return [
            l.strip() for l in self._header_block()
            if l.strip() and not l.strip().startswith("#")
        ]

    def test_defer_is_an_op_of_the_header_block(self):
        # `defer` is load-bearing: it makes `set` REPLACE the Django copies at
        # response-write time. Non-deferred, the response ships two HSTS lines
        # and Caddy's Referrer-Policy silently loses (last valid value wins).
        self.assertIn("defer", self._ops())

    def test_csp_locks_api_only_origin(self):
        csp = [op for op in self._ops() if op.startswith("Content-Security-Policy")]
        self.assertEqual(len(csp), 1, f"expected one CSP op, got {csp}")
        # API-only origin: every fetch/embed/base/form vector closed. Any
        # future first-party HTML frontend must relax these DELIBERATELY.
        for directive in (
            "default-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        ):
            self.assertIn(directive, csp[0])

    def test_referrer_policy_no_referrer(self):
        self.assertIn('Referrer-Policy "no-referrer"', self._ops())

    def test_permissions_policy_denies_powerful_features(self):
        pp = [op for op in self._ops() if op.startswith("Permissions-Policy")]
        self.assertEqual(len(pp), 1, f"expected one Permissions-Policy op, got {pp}")
        for feature in (
            "camera=()",
            "microphone=()",
            "geolocation=()",
            "payment=()",
            "usb=()",
        ):
            self.assertIn(feature, pp[0])

    def test_hsts_kept_with_preload(self):
        # The deferred block must not LOSE the pre-existing HSTS pin while
        # gaining the new headers.
        self.assertIn(
            'Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"',
            self._ops(),
        )

    def test_real_ip_override_survives(self):
        # P0 Root C.1: the proxy derives the client IP from the real TCP peer
        # and OVERRIDES client-supplied forwarding headers -- the header-block
        # rework must not disturb it (rate limiting keys off X-Real-IP).
        self.assertIn("header_up X-Real-IP {remote_host}", self.text)

    def test_access_log_redacts_query_string(self):
        # The edge access log must strip the URI query string: it carries PII
        # (user questions, browsing URLs) and historically credentials (the
        # removed ?token= path). This is the edge half of the same redaction
        # gunicorn.conf.py applies app-side (%(U)s, no %(q)s) -- pinned in
        # test_gunicorn_conf.py; both layers must agree or one log stream
        # keeps leaking what the other redacts. Bare `format console` (the
        # pre-hygiene shape) must not come back.
        lines = [
            l.strip() for l in self.text.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        self.assertIn("format filter {", lines)
        self.assertIn('request>uri regexp \\?.* ""', lines)
        self.assertNotIn("format console", lines)
