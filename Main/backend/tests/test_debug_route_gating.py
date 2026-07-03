"""Route-level gating for /debug/memory/ (Root G hygiene).

api.views_debug discloses allocator tracebacks (absolute file paths + line numbers) and lets
a caller start tracemalloc — persistent RAM overhead, a DoS lever — behind one static shared
token. Token auth itself is covered in tests/test_debug_memory_endpoint.py; this file pins
the OUTER layer: django_config/urls.py registers the route only under `if settings.DEBUG:`,
so production (DJANGO_DEBUG unset -> False) 404s before the view is reachable, even if
DEBUG_MEMORY_TOKEN leaks or gets set by mistake.

Hermeticity: CI runs with no .env (DEBUG=False), but a local backend/.env sets
DJANGO_DEBUG=True, so these tests never trust the ambient flag — they re-evaluate the
URLconf's import-time `if settings.DEBUG:` by reloading django_config.urls under an explicit
override_settings(DEBUG=...) and restore the ambient URLconf in tearDown.
"""
import ast
import importlib
from pathlib import Path

from django.test import SimpleTestCase, override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse

import django_config.urls as project_urls

URLS_PATH = Path(project_urls.__file__).resolve()

# Shared knobs for driving a REAL request through the middleware stack under bare pytest
# (no Django test runner, so setup_test_environment() never adds 'testserver' to
# ALLOWED_HOSTS). SECURE_SSL_REDIRECT must be pinned off: with DEBUG=False the settings
# default it True, and SecurityMiddleware would 301 the plain-HTTP test request BEFORE URL
# resolution (the 301-trap, PR #313) — we'd assert on the redirect, not on routing.
_REQUEST_KNOBS = {
    "ALLOWED_HOSTS": ["testserver"],
    "SECURE_SSL_REDIRECT": False,
}


def _reload_urlconf():
    """Re-run django_config.urls' module body so its `if settings.DEBUG:` re-evaluates
    against the CURRENTLY ACTIVE settings, then drop Django's cached resolver."""
    importlib.reload(project_urls)
    clear_url_caches()


class DebugRouteGatingTests(SimpleTestCase):
    def tearDown(self):
        # Rebuild the URLconf from ambient settings so the reload never bleeds into other
        # test modules that resolve against the default ROOT_URLCONF.
        _reload_urlconf()

    def test_route_absent_when_debug_false(self):
        """Production posture (DEBUG=False): no reverse() name, and the URL 404s."""
        with override_settings(DEBUG=False, **_REQUEST_KNOBS):
            _reload_urlconf()
            with self.assertRaises(NoReverseMatch):
                reverse("debug_memory")
            response = self.client.get("/debug/memory/")
            self.assertEqual(
                response.status_code, 404,
                "/debug/memory/ must not be routed at all when DEBUG=False — a 403 here "
                "would mean the view is reachable and only the token stands in the way",
            )

    def test_route_present_when_debug_true(self):
        """The gate is conditional registration, not deletion: under DEBUG=True the route
        resolves and hits the view's token check (403 without a token — NOT 404)."""
        with override_settings(DEBUG=True, **_REQUEST_KNOBS):
            _reload_urlconf()
            self.assertEqual(reverse("debug_memory"), "/debug/memory/")
            response = self.client.get("/debug/memory/")
            self.assertEqual(response.status_code, 403)


# ── Structural pin on the source (grep-sentinel style) ────────────────────────────────────
# The runtime tests above prove behavior for the current module body; this pins the SHAPE of
# urls.py so a refactor that hoists the registration out of the `if settings.DEBUG:` block
# (or adds a second, unguarded copy) fails loudly even if it keeps the tests' env happy.

class _DebugRouteVisitor(ast.NodeVisitor):
    """Collect every `path('debug/memory/', ...)` call and whether it sits inside an
    `if settings.DEBUG:` body. `if not settings.DEBUG:` / orelse branches count as
    UNguarded — only the plain-truthy body is a valid home for the registration."""

    def __init__(self):
        self.registrations = []  # (lineno, guarded)
        self._guard_stack = []

    @staticmethod
    def _is_settings_debug(test):
        return (
            isinstance(test, ast.Attribute)
            and test.attr == "DEBUG"
            and isinstance(test.value, ast.Name)
            and test.value.id == "settings"
        )

    def visit_If(self, node):
        self._guard_stack.append(self._is_settings_debug(node.test))
        for child in node.body:
            self.visit(child)
        self._guard_stack.pop()
        for child in node.orelse:  # else-branch of the guard is NOT gated
            self.visit(child)

    def visit_Call(self, node):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "path"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "debug/memory/"
        ):
            self.registrations.append((node.lineno, any(self._guard_stack)))
        self.generic_visit(node)


class DebugRouteStructuralPinTests(SimpleTestCase):
    def test_debug_memory_registered_exactly_once_inside_settings_debug_block(self):
        source = URLS_PATH.read_text(encoding="utf-8")

        # Cheap sentinel first: exactly one mention of the route string in urls.py, so a
        # duplicated (possibly unguarded) registration can't hide behind the AST check.
        self.assertEqual(
            source.count("'debug/memory/'"), 1,
            "expected exactly one 'debug/memory/' registration in django_config/urls.py",
        )

        visitor = _DebugRouteVisitor()
        visitor.visit(ast.parse(source))

        self.assertEqual(
            len(visitor.registrations), 1,
            f"expected exactly one path('debug/memory/', ...) call, "
            f"found {len(visitor.registrations)}",
        )
        lineno, guarded = visitor.registrations[0]
        self.assertTrue(
            guarded,
            f"urls.py line {lineno}: path('debug/memory/', ...) must live inside an "
            "`if settings.DEBUG:` block — unguarded registration exposes the diagnostic "
            "endpoint (allocator tracebacks + tracemalloc control) in production",
        )
