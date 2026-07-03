"""Root-G endpoint-protection hygiene: limiter coverage, method gates, health pin.

Tier 1 — coverage sentinel: EVERY routed view except ``api.views.health`` must
carry the production decoration
``@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT,
method=ALL, block=True)`` where ``ALL`` is the imported ``django_ratelimit.ALL``
sentinel. The sentinel form matters: P0 Root C proved the STRING ``method='ALL'``
never matches ``core._method_match``'s ``(None,)`` tuple, silently disabling the
limiter — so the structural scan also forbids the string form at any call site.

Tier 2 — method gates: ``clear_messages`` mutates state on a CSRF-exempt
endpoint so it is POST-only; the chat/agent surface reads query params only so
it is GET-only.

health/ pin: django-ratelimit fails CLOSED when the counter cache is
unreachable (``get_usage`` returns ``should_limit`` for a ``None`` count, and
backend exceptions propagate as 500s), so a decorated health endpoint would go
down WITH Redis and turn an outage into an LB-driven restart cascade. health
must stay un-ratelimited (structural + behavioral at 1-req/min headroom) and
fully independent of the cache backend.

Run: uv run pytest tests/test_endpoint_protection.py -v
"""
import ast
import importlib
import json
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.cache.backends.base import BaseCache
from django.test import RequestFactory, SimpleTestCase, override_settings
from django_ratelimit.exceptions import Ratelimited

import api.views as views_module
import django_config.urls as urlconf

# Captured at import time, BEFORE any override_settings is active, so the
# restore-reload in AddWebtextRatelimit429Tests.tearDownClass re-binds the
# decorators at the real production rate (see comment there).
_REAL_RATE = settings.API_RATE_LIMIT

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Every dir that may legitimately contain a @ratelimit call site. Scanned for
# the forbidden string-method form; .venv lives outside all of these.
RATELIMIT_SCAN_DIRS = ("api", "django_config", "datascraper", "tests")

CANONICAL_KEY = "api.identity.ratelimit_key"

# (module, view) pairs allowed to skip the production limiter. Every entry
# needs a WHY; any other routed-but-undecorated view fails the sentinel.
RATELIMIT_EXEMPT = {
    ("api.views", "health"): (
        "LB probe: the limiter fails closed when the counter cache is down, "
        "so decorating health couples the probe to Redis availability"
    ),
    ("api.views_debug", "debug_memory"): (
        "fail-closed token gate (403 unless DEBUG_MEMORY_TOKEN matches, and "
        "403 when the token is unset) and DEBUG-gated route registration in "
        "urls.py; ratelimit decoration for views_debug is owned by a separate "
        "hygiene item — drop this entry when it lands"
    ),
}


# ── structural helpers (pure AST, immune to importlib.reload churn) ──────────


def _routed_views():
    """(module, name) for every view wired in django_config.urls.

    functools.wraps preserves __module__/__name__ through the csrf/method/
    ratelimit wrapper stack, so these names key straight into the AST maps.
    """
    return sorted(
        {(p.callback.__module__, p.callback.__name__) for p in urlconf.urlpatterns}
    )


def _module_ast(module_name):
    source = Path(importlib.import_module(module_name).__file__)
    return ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def _view_decorators(module_name):
    """Top-level function name -> list of decorator ast.Call nodes."""
    return {
        node.name: [d for d in node.decorator_list if isinstance(d, ast.Call)]
        for node in _module_ast(module_name).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _is_ratelimit_call(call):
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name == "ratelimit"


def _call_kwargs(call):
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _is_production_ratelimit(call):
    """Match the canonical decoration, kwarg by kwarg.

    ``method`` must be the imported sentinel (a Name/Attribute reference named
    ALL), never a string constant — the exact silent-no-op bug of P0 Root C.
    """
    if not _is_ratelimit_call(call):
        return False
    kw = _call_kwargs(call)
    key = kw.get("key")
    rate = kw.get("rate")
    method = kw.get("method")
    block = kw.get("block")
    return (
        isinstance(key, ast.Constant) and key.value == CANONICAL_KEY
        and isinstance(rate, ast.Attribute) and rate.attr == "API_RATE_LIMIT"
        and isinstance(rate.value, ast.Name) and rate.value.id == "settings"
        and (
            (isinstance(method, ast.Name) and method.id == "ALL")
            or (isinstance(method, ast.Attribute) and method.attr == "ALL")
        )
        and isinstance(block, ast.Constant) and block.value is True
    )


class RatelimitCoverageSentinelTests(SimpleTestCase):
    """Structural pin: adding a route without the limiter breaks the build."""

    def test_every_routed_view_carries_production_ratelimit(self):
        decorators_cache = {}
        missing = []
        for module_name, view_name in _routed_views():
            if (module_name, view_name) in RATELIMIT_EXEMPT:
                continue
            if module_name not in decorators_cache:
                decorators_cache[module_name] = _view_decorators(module_name)
            calls = decorators_cache[module_name].get(view_name, [])
            if not any(_is_production_ratelimit(c) for c in calls):
                missing.append(f"{module_name}.{view_name}")
        self.assertEqual(
            missing, [],
            "routed view(s) without the production @ratelimit decoration "
            "(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT, "
            "method=ALL sentinel, block=True) and not in RATELIMIT_EXEMPT: "
            f"{missing}",
        )

    def test_exemptions_reference_real_views(self):
        # A stale exemption (view renamed/deleted) must be pruned, not carried.
        # Existence, not routing, is asserted: debug/memory is registered under
        # `if settings.DEBUG` in urls.py, so it is absent from urlpatterns in
        # the DEBUG=False test default yet still needs its exemption for
        # DEBUG=True runs.
        for module_name, view_name in RATELIMIT_EXEMPT:
            module = importlib.import_module(module_name)
            self.assertTrue(
                callable(getattr(module, view_name, None)),
                f"RATELIMIT_EXEMPT entry {module_name}.{view_name} no longer "
                "exists — remove it",
            )

    def test_health_is_structurally_unratelimited(self):
        # The exemption above only *allows* health to skip the limiter; this
        # pins that nobody decorates it later (see HealthIndependenceTests for
        # the WHY: fail-closed limiter + Redis outage = dead LB probe).
        calls = _view_decorators("api.views").get("health", [])
        self.assertFalse(
            any(_is_ratelimit_call(c) for c in calls),
            "api.views.health must NEVER carry @ratelimit",
        )

    def test_no_string_method_all_form_at_any_call_site(self):
        offenders = []
        seen = 0
        for scan_dir in RATELIMIT_SCAN_DIRS:
            for source in sorted((BACKEND_DIR / scan_dir).rglob("*.py")):
                tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and _is_ratelimit_call(node):
                        seen += 1
                        method = _call_kwargs(node).get("method")
                        if isinstance(method, ast.Constant):
                            offenders.append(f"{source}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "ratelimit(method=<string>) is a silent no-op (never matches the "
            f"django_ratelimit.ALL sentinel tuple); found at: {offenders}",
        )
        # Vacuity guard: a scanner that finds no call sites proves nothing.
        self.assertGreaterEqual(
            seen, 10, "string-ALL scan found implausibly few @ratelimit call sites"
        )


# ── behavioral: the NEW Tier-1 decoration actually fires (no decoration theater)


# Tight per-identity limit + in-process LocMemCache so counters are
# deterministic and isolated from the base FileBasedCache (same mechanics as
# tests/test_ratelimit_enforced.py).
RL_OVERRIDES = override_settings(
    RATELIMIT_ENABLE=True,
    API_RATE_LIMIT="1/m",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "endpoint-protection-429-tests",
        }
    },
)


@RL_OVERRIDES
class AddWebtextRatelimit429Tests(SimpleTestCase):
    """input_webtext/ (a previously un-throttled ingest sink) must block the
    second request from one identity once decorated."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Re-import api.views with API_RATE_LIMIT='1/m' in effect so the
        # decorators re-bind at a tight rate while keeping their real key= and
        # method= arguments — the production wiring is genuinely under test.
        cls._reloaded = importlib.reload(views_module)
        cls.add_webtext = staticmethod(cls._reloaded.add_webtext)

    @classmethod
    def tearDownClass(cls):
        # tearDownClass runs BEFORE Django disables the class-level override
        # (that happens in a class *cleanup*), so a bare reload here would
        # re-bind the decorators at 1/m and leak the tight limiter into every
        # later test file in the same process — the exact leak that lives in
        # test_ratelimit_enforced.py and 429s test_axiom_views when the suites
        # share one process. Re-override to the real rate for the restore.
        with override_settings(API_RATE_LIMIT=_REAL_RATE):
            importlib.reload(views_module)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _trusted_proxy_post(self, real_ip="198.51.100.77"):
        # REMOTE_ADDR=127.0.0.1 is a default TRUSTED_PROXY, so get_client_ip()
        # honors X-Real-IP and both hits share one identity bucket. The body is
        # an empty JSON object on purpose: add_webtext 400s on 'No text content
        # provided' before any session/context work, so the limiter is the only
        # stateful actor and the test needs no SessionMiddleware. (A bare
        # factory.post() would ship a multipart boundary that json.loads
        # rejects into the generic 500 path instead.)
        req = self.factory.post(
            "/input_webtext/", data="{}", content_type="application/json"
        )
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        req.META["HTTP_X_REAL_IP"] = real_ip
        return req

    def test_second_post_from_same_identity_is_rate_limited(self):
        first = self._trusted_proxy_post()
        resp = self.add_webtext(first)
        self.assertFalse(
            getattr(first, "limited", False),
            "first request must NOT be limited (fresh 1/m bucket)",
        )
        self.assertEqual(resp.status_code, 400)  # empty-body short-circuit

        second = self._trusted_proxy_post()
        with self.assertRaises(
            Ratelimited,
            msg="second request was NOT blocked -> the new add_webtext "
            "decoration is a no-op",
        ):
            self.add_webtext(second)
        self.assertTrue(
            getattr(second, "limited", False),
            "request.limited must be True once the bucket is exhausted",
        )

    def test_distinct_identities_keep_independent_buckets(self):
        a1 = self._trusted_proxy_post(real_ip="203.0.113.21")
        self.add_webtext(a1)
        with self.assertRaises(Ratelimited):
            self.add_webtext(self._trusted_proxy_post(real_ip="203.0.113.21"))

        # Fresh identity -> fresh bucket -> allowed through to the view body.
        b1 = self._trusted_proxy_post(real_ip="203.0.113.22")
        resp = self.add_webtext(b1)
        self.assertFalse(getattr(b1, "limited", False))
        self.assertEqual(resp.status_code, 400)


# ── Tier 2: method gates ─────────────────────────────────────────────────────


METHOD_GATE_OVERRIDES = override_settings(
    # RATELIMIT_ENABLE is read per-request in get_usage, so no module reload is
    # needed; disabling it isolates the method gate as the only actor.
    RATELIMIT_ENABLE=False,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "endpoint-protection-method-tests",
        }
    },
)


@METHOD_GATE_OVERRIDES
class MethodGateTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_clear_messages_is_405(self):
        resp = views_module.clear(self.factory.get("/clear_messages/"))
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp["Allow"], "POST")

    def test_put_clear_messages_is_405(self):
        resp = views_module.clear(self.factory.put("/clear_messages/"))
        self.assertEqual(resp.status_code, 405)

    def test_post_clear_messages_succeeds(self):
        # RequestFactory attaches no session: _cookie_root falls back to a
        # fresh uuid and clear_session is a plain cache.delete on locmem, so
        # the happy path is hermetic.
        resp = views_module.clear(self.factory.post("/clear_messages/"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["status"], "success")

    def test_post_to_get_only_chat_view_is_405(self):
        resp = views_module.chat_response(
            self.factory.post("/get_chat_response/", data={"question": "hi"})
        )
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp["Allow"], "GET")


# ── health/ regression pin: un-ratelimited AND cache-backend independent ─────


class RaisingCache(BaseCache):
    """Counter cache that is hard-down: EVERY op raises.

    Simulates a Redis outage under settings_prod's shared cache. health must
    not notice; a (wrongly) ratelimited health would blow up inside
    get_usage's cache.add before the view body ever ran.
    """

    def __init__(self, location, params):
        super().__init__(params)

    def _down(self, *args, **kwargs):
        raise RuntimeError("counter cache unavailable (simulated Redis outage)")

    add = _down
    get = _down
    set = _down
    touch = _down
    incr = _down
    decr = _down
    delete = _down
    clear = _down
    has_key = _down


@override_settings(
    RATELIMIT_ENABLE=True,
    CACHES={"default": {"BACKEND": "tests.test_endpoint_protection.RaisingCache"}},
)
class HealthIndependenceTests(SimpleTestCase):
    def test_health_stays_200_twice_with_counter_cache_down(self):
        # Two calls: catches both failure shapes of an accidental decoration —
        # a cache exception on call 1 and a fail-closed 429 path on call 2.
        factory = RequestFactory()
        for attempt in (1, 2):
            resp = views_module.health(factory.get("/health/"))
            self.assertEqual(
                resp.status_code, 200,
                f"health attempt {attempt} must be 200 even with the cache down",
            )
            self.assertEqual(json.loads(resp.content)["status"], "healthy")
