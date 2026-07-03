"""Root-G endpoint-protection hygiene: limiter coverage, method gates, health pin.

Tier 1 — coverage sentinel: EVERY routed view except ``api.views.health`` must
carry the production decoration
``@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT,
method=ALL, block=True)`` where ``ALL`` is the imported ``django_ratelimit.ALL``
sentinel. The sentinel form matters: P0 Root C proved the STRING ``method='ALL'``
never matches ``core._method_match``'s ``(None,)`` tuple, silently disabling the
limiter — so the structural scan also forbids the string form at any call site.

Tier 2/3 — method gates: ``clear_messages`` mutates state on a CSRF-exempt
endpoint so it is POST-only. The chat surface (four views) dual-accepts
GET+POST during the Tier-3 migration window: POST carries a flat all-string
JSON body (same keys as the query params, body wins over a same-key query
value, syntactically invalid JSON -> 400) while GET behavior stays unchanged
until the POST-only flip lands in a later PR. PUT/DELETE stay 405.

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
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.cache import cache
from django.core.cache.backends.base import BaseCache
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
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


# ── Tier 2/3: method gates + dual-accept chat surface ───────────────────────


# The four dual-accept chat views (Tier 3 phase 1). view name -> routed path.
CHAT_VIEWS = {
    "chat_response": "/get_chat_response/",
    "adv_response": "/get_adv_response/",
    "chat_response_stream": "/get_chat_response_stream/",
    "adv_response_stream": "/get_adv_response_stream/",
}


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

    def test_post_to_chat_views_passes_method_gate(self):
        # Tier 3 phase 1 flip of the former GET-only pin: POST is accepted on
        # the whole chat surface. An empty JSON body short-circuits at the
        # 'No question provided' 400 BEFORE any session/LLM work, so this pins
        # the gate (400, not 405) hermetically with zero mocks. Full POST
        # behavior is pinned by DualAcceptChatViewTests below.
        for view_name, path in CHAT_VIEWS.items():
            with self.subTest(view=view_name):
                req = self.factory.post(path, data="{}", content_type="application/json")
                resp = getattr(views_module, view_name)(req)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(json.loads(resp.content)["error"], "No question provided")

    def test_put_and_delete_on_chat_views_stay_405(self):
        for view_name, path in CHAT_VIEWS.items():
            for method in ("put", "delete"):
                with self.subTest(view=view_name, method=method):
                    req = getattr(self.factory, method)(path)
                    resp = getattr(views_module, view_name)(req)
                    self.assertEqual(resp.status_code, 405)
                    self.assertEqual(resp["Allow"], "GET, POST")


# ── Tier 3 phase 1: dual-accept (GET+POST) behavior on the chat surface ─────


def _noop_slot():
    """agent_run_slot stand-in whose context manager acquires and releases."""
    cm = MagicMock()
    cm.__enter__.return_value = None
    cm.__exit__.return_value = False
    return MagicMock(return_value=cm)


def _ctx_mock():
    ctx = MagicMock()
    ctx.get_formatted_messages_for_api.return_value = []
    ctx.get_session_stats.return_value = {
        "mode": "thinking",
        "message_count": 1,
        "token_count": 2,
        "fetched_context_counts": {},
    }
    return ctx


async def _one_chunk_agent_gen():
    yield "Hello"


async def _one_chunk_research_gen():
    yield ("Hello", [])


def _invoke_chat_view(view_name, request):
    """Run a chat view with the LLM layer mocked out.

    Returns ``(response, captured, payload)``: ``captured`` holds the args/
    kwargs that reached the downstream datascraper call — the proof a param
    actually traversed the view logic rather than just the decorator stack —
    and ``payload`` is the fully-drained body (SSE bytes for the *_stream
    views, JSON bytes otherwise; draining also exercises the slot-release
    finally in the stream generators).
    """
    captured = {}

    def _record(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    def fake_agent(*args, **kwargs):
        _record(*args, **kwargs)
        return "ok", []

    def fake_advanced(*args, **kwargs):
        _record(*args, **kwargs)
        return "ok", []

    def fake_agent_stream(*args, **kwargs):
        _record(*args, **kwargs)
        return _one_chunk_agent_gen(), {"final_output": "ok"}

    def fake_advanced_stream(*args, **kwargs):
        _record(*args, **kwargs)
        return _one_chunk_research_gen(), {}

    with patch("api.views.agent_run_slot", _noop_slot()), \
         patch("api.views.get_context_manager", return_value=_ctx_mock()), \
         patch("api.views.get_context_integration", return_value=MagicMock()), \
         patch("api.views.get_claims", return_value=[]), \
         patch("api.views._wrap_for_client", side_effect=lambda prose, sid: prose), \
         patch("api.views.build_xbrl_sources", return_value=[]), \
         patch("api.views.merge_xbrl_sources", side_effect=lambda sources, xbrl: sources), \
         patch("api.views.ds.create_agent_response", side_effect=fake_agent), \
         patch("api.views.ds.create_advanced_response", side_effect=fake_advanced), \
         patch("api.views.ds.create_agent_response_stream", side_effect=fake_agent_stream), \
         patch("api.views.ds.create_advanced_response_streaming", side_effect=fake_advanced_stream):
        resp = getattr(views_module, view_name)(request)
        if hasattr(resp, "streaming_content"):
            payload = b"".join(resp.streaming_content)
        else:
            payload = resp.content
    return resp, captured, payload


def _downstream_signature(view_name, captured):
    """(question, model, third, user_timezone, user_time) as received by the
    mocked datascraper call. ``third`` is current_url for the thinking views
    and the parsed preferred_links list for the research views."""
    args = captured.get("args", ())
    kwargs = captured.get("kwargs", {})
    if view_name == "adv_response_stream":
        question, _messages, model, preferred_links = args
        return (question, model, preferred_links,
                kwargs.get("user_timezone"), kwargs.get("user_time"))
    if view_name == "adv_response":
        return (kwargs["user_input"], kwargs["model"], kwargs["preferred_links"],
                kwargs.get("user_timezone"), kwargs.get("user_time"))
    return (kwargs["user_input"], kwargs["model"], kwargs["current_url"],
            kwargs.get("user_timezone"), kwargs.get("user_time"))


@METHOD_GATE_OVERRIDES
class DualAcceptChatViewTests(SimpleTestCase):
    """Frozen POST contract on the four chat views: flat all-string JSON body,
    same keys as the query params, body wins over query, GET unchanged."""

    maxDiff = None

    # One canonical param set, sent as GET query params and as the POST body.
    PARAMS = {
        "question": "parity question",
        "models": "gpt-4o-mini",
        "current_url": "https://example.com/page",
        "use_unified": "true",
        "user_timezone": "America/New_York",
        "user_time": "2026-07-03T10:30:00",
        "preferred_links": '["https://reuters.com"]',
        "session_id": "sub-42",
    }

    def setUp(self):
        self.factory = RequestFactory()

    def _post_json(self, path, payload, query=""):
        return self.factory.post(
            path + query, data=json.dumps(payload), content_type="application/json"
        )

    def test_post_json_body_reaches_downstream_with_get_parity(self):
        # (a) POST works on all four views AND behaves exactly like the GET
        # equivalent: the values that reach the mocked datascraper layer are
        # identical for both methods.
        for view_name, path in CHAT_VIEWS.items():
            with self.subTest(view=view_name):
                get_resp, get_captured, _ = _invoke_chat_view(
                    view_name, self.factory.get(path, data=self.PARAMS)
                )
                post_resp, post_captured, _ = _invoke_chat_view(
                    view_name, self._post_json(path, self.PARAMS)
                )
                self.assertEqual(get_resp.status_code, 200)
                self.assertEqual(post_resp.status_code, 200)
                get_sig = _downstream_signature(view_name, get_captured)
                self.assertEqual(get_sig, _downstream_signature(view_name, post_captured))
                self.assertEqual(get_sig[0], "parity question")
                self.assertEqual(get_sig[1], "gpt-4o-mini")

    def test_body_wins_over_query_and_merge_keeps_query_only_keys(self):
        # (b) same-key conflict: the JSON body value wins; a key present only
        # in the query string still applies (merge, not replace).
        for view_name, path in CHAT_VIEWS.items():
            with self.subTest(view=view_name):
                req = self._post_json(
                    path,
                    {"question": "from body"},
                    query="?question=from+query&models=query-model",
                )
                resp, captured, _ = _invoke_chat_view(view_name, req)
                self.assertEqual(resp.status_code, 200)
                sig = _downstream_signature(view_name, captured)
                self.assertEqual(sig[0], "from body")
                self.assertEqual(sig[1], "query-model")

    def test_get_behavior_unchanged(self):
        # (c) plain GET keeps working with the same response shape: JSON with
        # 'resp' for the non-stream views, an SSE stream ending in a done
        # frame for the *_stream views.
        for view_name, path in CHAT_VIEWS.items():
            with self.subTest(view=view_name):
                req = self.factory.get(path, data={"question": "get question", "models": "m1"})
                resp, captured, payload = _invoke_chat_view(view_name, req)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(_downstream_signature(view_name, captured)[0], "get question")
                if view_name.endswith("_stream"):
                    self.assertEqual(resp["Content-Type"], "text/event-stream")
                    self.assertIn(b'event: connected', payload)
                    self.assertIn(b'"done": true', payload)
                else:
                    self.assertEqual(json.loads(payload)["resp"], {"m1": "ok"})

    @staticmethod
    def _normalize_sse(payload):
        # Two independent requests can never be literally byte-identical: the
        # final frame embeds a fresh per-request session uuid and a wall-clock
        # response_time_ms. Normalize exactly those two fields; every other
        # byte must match.
        payload = re.sub(rb'"session_id": "[0-9a-f]+"', b'"session_id": "X"', payload)
        return re.sub(rb'"response_time_ms": \d+', b'"response_time_ms": 0', payload)

    def test_post_sse_response_matches_get_sse_response(self):
        # Stream-contract parity: same mocked downstream, same frames.
        for view_name in ("chat_response_stream", "adv_response_stream"):
            path = CHAT_VIEWS[view_name]
            with self.subTest(view=view_name):
                _, _, get_payload = _invoke_chat_view(
                    view_name, self.factory.get(path, data={"question": "q", "models": "m1"})
                )
                _, _, post_payload = _invoke_chat_view(
                    view_name, self._post_json(path, {"question": "q", "models": "m1"})
                )
                self.assertEqual(
                    self._normalize_sse(get_payload), self._normalize_sse(post_payload)
                )

    def test_malformed_json_post_is_400(self):
        # (f) chosen semantics: a syntactically invalid JSON body on POST is a
        # loud client error (400 'invalid JSON body'), never a 500 and never a
        # silent fallback to query params.
        for view_name, path in CHAT_VIEWS.items():
            with self.subTest(view=view_name):
                req = self.factory.post(
                    path + "?question=from+query",
                    data="{not json",
                    content_type="application/json",
                )
                resp = getattr(views_module, view_name)(req)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(json.loads(resp.content)["error"], "invalid JSON body")

    def test_valid_but_non_object_json_body_falls_back_to_query(self):
        # (f) companion: valid JSON that is not an object (here: a list) is
        # tolerated as an empty body — same stance as
        # datascraper.session_key._caller_session_id.
        req = self.factory.post(
            "/get_chat_response/?question=from+query",
            data="[]",
            content_type="application/json",
        )
        resp, captured, _ = _invoke_chat_view("chat_response", req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_downstream_signature("chat_response", captured)[0], "from query")

    def test_non_string_body_values_are_ignored(self):
        # Frozen contract: ALL body values are strings. A non-string value is
        # treated as absent (query fallback applies) instead of type-confusing
        # the view (e.g. .split on an int).
        req = self.factory.post(
            "/get_chat_response/?models=query-model",
            data=json.dumps({"question": "q", "models": 123}),
            content_type="application/json",
        )
        resp, captured, _ = _invoke_chat_view("chat_response", req)
        self.assertEqual(resp.status_code, 200)
        sig = _downstream_signature("chat_response", captured)
        self.assertEqual(sig[0], "q")
        self.assertEqual(sig[1], "query-model")


CSRF_CLIENT_OVERRIDES = override_settings(
    RATELIMIT_ENABLE=False,
    # The test client sends Host: testserver over plain http; production
    # settings would otherwise 400 (ALLOWED_HOSTS) or 301 (SECURE_SSL_REDIRECT
    # defaults True when DEBUG=False — the Caddy-terminated prod posture).
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=False,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "endpoint-protection-csrf-tests",
        }
    },
)


@CSRF_CLIENT_OVERRIDES
class CsrfExemptDualAcceptTests(SimpleTestCase):
    """(e) POST must survive CSRF enforcement: the extension sends no CSRF
    token, so the dual-accept flip only works because the views are
    @csrf_exempt. Client(enforce_csrf_checks=True) drives the REAL middleware
    stack (CsrfViewMiddleware included); reaching the view body — the
    'No question provided' 400 short-circuit, or log_question's 200 ack —
    instead of a 403 proves the exemption holds end-to-end through routing."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_post_without_csrf_token_reaches_chat_view_bodies(self):
        for view_name, path in CHAT_VIEWS.items():
            with self.subTest(view=view_name):
                resp = self.client.post(path, data="{}", content_type="application/json")
                self.assertNotEqual(resp.status_code, 403, "csrf_exempt does not hold")
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(json.loads(resp.content)["error"], "No question provided")

    def test_post_without_csrf_token_reaches_log_question(self):
        resp = self.client.post(
            "/log_question/",
            data=json.dumps({"question": "q", "button": "b", "current_url": "https://x.example/"}),
            content_type="application/json",
        )
        self.assertNotEqual(resp.status_code, 403, "csrf_exempt does not hold")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["status"], "success")


@METHOD_GATE_OVERRIDES
class LogQuestionDualAcceptTests(SimpleTestCase):
    """(g) log_question dual-accepts GET and POST with the same frozen body
    contract (key: question); PUT stays 405; malformed JSON is 400."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_get_still_works(self):
        req = self.factory.get(
            "/log_question/",
            data={"question": "why", "button": "thinking", "current_url": "https://x.example/"},
        )
        with self.assertLogs("api.views", level="INFO") as logs:
            resp = views_module.log_question(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["status"], "success")
        self.assertTrue(any("Interaction [thinking]" in line for line in logs.output))

    def test_post_json_body_reaches_the_log_line(self):
        req = self.factory.post(
            "/log_question/",
            data=json.dumps(
                {"question": "why", "button": "research", "current_url": "https://x.example/"}
            ),
            content_type="application/json",
        )
        with self.assertLogs("api.views", level="INFO") as logs:
            resp = views_module.log_question(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["status"], "success")
        joined = "\n".join(logs.output)
        self.assertIn("Interaction [research]", joined)
        self.assertIn("Q='why", joined)

    def test_body_wins_over_query(self):
        req = self.factory.post(
            "/log_question/?question=from+query&button=frombtn&current_url=https://x.example/",
            data=json.dumps({"question": "from body"}),
            content_type="application/json",
        )
        with self.assertLogs("api.views", level="INFO") as logs:
            resp = views_module.log_question(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Q='from body", "\n".join(logs.output))

    def test_malformed_json_post_is_400(self):
        req = self.factory.post(
            "/log_question/", data="{not json", content_type="application/json"
        )
        resp = views_module.log_question(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "invalid JSON body")

    def test_put_is_405(self):
        resp = views_module.log_question(self.factory.put("/log_question/"))
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp["Allow"], "GET, POST")


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
