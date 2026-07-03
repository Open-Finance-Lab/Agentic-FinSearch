"""Enforcement tests for agent_run_slot at the 4 agent views (P0 Root-C.3).

503-on-reject for ConcurrencyExceeded at all 4 views (mock slot) + a
BudgetExceeded variant, and slot RELEASE for the two streaming views on
normal stream exhaustion and on a mid-stream raise. (agent_chat_response,
formerly the 5th view, was deleted in Tier 3 phase 1 — zero consumers.)

SimpleTestCase, no DB (signed_cookies session). From Main/backend:
    uv run python manage.py test tests.test_agent_budget_enforce -v 2
"""
import os
from importlib import import_module
from unittest.mock import patch, MagicMock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_config.settings')

import django  # noqa: E402
from django.apps import apps as _django_apps  # noqa: E402

if not _django_apps.ready:
    django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.http import StreamingHttpResponse  # noqa: E402
from django.test import RequestFactory, SimpleTestCase  # noqa: E402

from api import views  # noqa: E402
from api import agent_budget  # noqa: E402
from api.agent_budget import (  # noqa: E402
    agent_run_slot,
    BudgetExceeded,
    ConcurrencyExceeded,
)
from api.identity import get_request_identity  # noqa: E402


def _attach_session(request):
    """Attach a real signed_cookies session store (needs no DB)."""
    engine = import_module(settings.SESSION_ENGINE)
    request.session = engine.SessionStore()
    return request


def _rejecting_slot(exc):
    """A drop-in for agent_run_slot whose context-manager __enter__ raises."""
    cm = MagicMock()
    cm.__enter__.side_effect = exc
    return MagicMock(return_value=cm)


async def _one_chunk_gen():
    yield "Hello"


async def _midstream_raise_gen():
    yield "Hi"
    raise RuntimeError("midstream boom")


class TestNonStreamSlotReject(SimpleTestCase):
    """Non-stream agent views must return 503 (not 500) when the slot is
    rejected. A session is attached so _get_session_id (which runs before
    the slot wrap) does not raise and mask the 503 as a 500."""

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _req(self, path):
        req = self.factory.get(path + '?question=hi&models=gpt-4o-mini')
        return _attach_session(req)

    def _assert_busy(self, resp):
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp['Retry-After'], '30')

    def test_chat_response_503_on_concurrency(self):
        req = self._req('/get_chat_response/')
        with patch('api.views.agent_run_slot', _rejecting_slot(ConcurrencyExceeded())), \
             patch('api.views.get_context_manager'), \
             patch('api.views.get_context_integration'):
            resp = views.chat_response(req)
        self._assert_busy(resp)

    def test_adv_response_503_on_concurrency(self):
        req = self._req('/get_adv_response/')
        with patch('api.views.agent_run_slot', _rejecting_slot(ConcurrencyExceeded())), \
             patch('api.views.get_context_manager'), \
             patch('api.views.get_context_integration'):
            resp = views.adv_response(req)
        self._assert_busy(resp)

    def test_chat_response_503_on_budget_exceeded(self):
        # BudgetExceeded variant: daily cap, same 503 contract.
        req = self._req('/get_chat_response/')
        with patch('api.views.agent_run_slot', _rejecting_slot(BudgetExceeded())), \
             patch('api.views.get_context_manager'), \
             patch('api.views.get_context_integration'):
            resp = views.chat_response(req)
        self._assert_busy(resp)


class TestStreamSlotReject(SimpleTestCase):
    """Streaming agent views enter the slot synchronously at the top, before
    _get_session_id, and must return 503 (not a 200 stream) on rejection."""

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _req(self, path):
        req = self.factory.get(path + '?question=hi&models=gpt-4o-mini')
        return _attach_session(req)

    def _assert_busy(self, resp):
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp['Retry-After'], '30')

    def test_chat_response_stream_503_on_concurrency(self):
        req = self._req('/get_chat_response_stream/')
        with patch('api.views.agent_run_slot', _rejecting_slot(ConcurrencyExceeded())), \
             patch('api.views.get_context_manager'), \
             patch('api.views.get_context_integration'):
            resp = views.chat_response_stream(req)
        self._assert_busy(resp)

    def test_adv_response_stream_503_on_concurrency(self):
        req = self._req('/get_adv_response_stream/')
        with patch('api.views.agent_run_slot', _rejecting_slot(ConcurrencyExceeded())), \
             patch('api.views.get_context_manager'), \
             patch('api.views.get_context_integration'):
            resp = views.adv_response_stream(req)
        self._assert_busy(resp)


class TestStreamSlotRelease(SimpleTestCase):
    """The streaming finally must release the slot on normal exhaustion AND on
    a mid-stream raise. Proven against the REAL slot with concurrency pinned to
    1: if the slot leaked, inflight stays at 1 and a fresh acquire raises."""

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _ctx(self):
        cm = MagicMock()
        cm.get_formatted_messages_for_api.return_value = []
        cm.get_session_stats.return_value = {'message_count': 1, 'token_count': 2}
        return cm

    def _run_stream_and_reacquire(self, stream_pair):
        req = _attach_session(
            self.factory.get('/get_chat_response_stream/?question=hi&models=gpt-4o-mini')
        )
        identity = get_request_identity(req)
        with patch.object(agent_budget, 'AGENT_MAX_CONCURRENCY', 1), \
             patch('api.views.get_context_manager', return_value=self._ctx()), \
             patch('api.views.get_context_integration', return_value=MagicMock()), \
             patch('api.views.build_xbrl_sources', return_value=[]), \
             patch('api.views._wrap_for_client', side_effect=lambda s, sid: s), \
             patch('api.views.ds.create_agent_response_stream', return_value=stream_pair):
            resp = views.chat_response_stream(req)
            self.assertIsInstance(resp, StreamingHttpResponse)
            # Drive the generator to completion -> outermost finally releases.
            b''.join(resp.streaming_content)
            reacquired = False
            try:
                with agent_run_slot(identity):
                    reacquired = True
            except ConcurrencyExceeded:
                reacquired = False
        return reacquired

    def test_release_on_stream_exhaustion(self):
        reacquired = self._run_stream_and_reacquire(
            (_one_chunk_gen(), {'final_output': 'Hello'})
        )
        self.assertTrue(reacquired, 'slot leaked: inflight did not return to 0 after exhaustion')

    def test_release_on_midstream_raise(self):
        reacquired = self._run_stream_and_reacquire(
            (_midstream_raise_gen(), {'final_output': ''})
        )
        self.assertTrue(reacquired, 'slot leaked: inflight did not return to 0 after mid-stream raise')

    def test_release_on_client_disconnect_generatorexit(self):
        """Regression for the v1 wedge: a client disconnect mid-stream must
        release the slot. We start the stream, consume one chunk so the
        generator is suspended mid-body with the slot HELD, then simulate the
        disconnect by closing the underlying generator (injects GeneratorExit,
        exactly what the WSGI/ASGI server does on disconnect). GeneratorExit is
        a BaseException the inner `except Exception` cannot swallow, so only the
        outermost generator finally releases the slot. With concurrency pinned
        to 1, a leak here keeps inflight at 1 and wedges every later request.

        GeneratorExit is injected directly into event_stream's generator via
        resp._iterator.close(): StreamingHttpResponse.streaming_content returns
        a fresh map() wrapper over self._iterator on each access (so closing the
        wrapper would not reach event_stream), whereas resp._iterator IS the
        original event_stream generator object.
        """
        req = _attach_session(
            self.factory.get('/get_chat_response_stream/?question=hi&models=gpt-4o-mini')
        )
        identity = get_request_identity(req)
        with patch.object(agent_budget, 'AGENT_MAX_CONCURRENCY', 1), \
             patch('api.views.get_context_manager', return_value=self._ctx()), \
             patch('api.views.get_context_integration', return_value=MagicMock()), \
             patch('api.views.build_xbrl_sources', return_value=[]), \
             patch('api.views._wrap_for_client', side_effect=lambda s, sid: s), \
             patch('api.views.ds.create_agent_response_stream',
                   return_value=(_one_chunk_gen(), {'final_output': 'Hello'})):
            resp = views.chat_response_stream(req)
            self.assertIsInstance(resp, StreamingHttpResponse)

            # Begin iterating: pull one chunk so event_stream is suspended
            # mid-body and the slot is held (inflight == 1, at the cap of 1).
            chunks = iter(resp.streaming_content)
            next(chunks)
            self.assertEqual(
                cache.get('agent:inflight'), 1,
                'precondition: slot should be held mid-stream before disconnect',
            )

            # Simulate the client disconnect: close the ORIGINAL generator,
            # injecting GeneratorExit into the suspended event_stream.
            resp._iterator.close()

            self.assertEqual(
                cache.get('agent:inflight'), 0,
                'slot leaked: inflight did not return to 0 after client disconnect',
            )
            reacquired = False
            try:
                with agent_run_slot(identity):
                    reacquired = True
            except ConcurrencyExceeded:
                reacquired = False
        self.assertTrue(
            reacquired,
            'slot leaked: a fresh acquire was wedged after client disconnect',
        )
