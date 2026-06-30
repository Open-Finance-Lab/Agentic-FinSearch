"""Tests for the cookie-bound conversation-key derivation (P1 C-session/IDOR).

The conversation/history cache key must be rooted in the SIGNED session cookie,
not in the caller-supplied ``session_id``. A second browser (a different signed
cookie) is modeled by a separate SessionStore instance; the SAME browser across
turns is modeled by reusing the SAME SessionStore (its payload is what the
signed cookie carries).

SimpleTestCase, no DB. Run from Main/backend:
    uv run python manage.py test tests.test_session_key -v 2
"""
import json
from importlib import import_module
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase

from datascraper.session_key import derive_conversation_key

_ENGINE = import_module(settings.SESSION_ENGINE)


def _request(session_id=None, store=None, path="/api/chat/"):
    """Build a GET request, optionally with a caller-supplied session_id and a
    pre-existing signed-cookie SessionStore (the cookie payload)."""
    rf = RequestFactory()
    req = rf.get(path, {"session_id": session_id} if session_id else None)
    if store is not None:
        req.session = store
    return req


class TestDeriveConversationKey(SimpleTestCase):
    def test_idor_same_session_id_different_cookies_yield_different_keys(self):
        # Caller A and caller B both pass the SAME guessed session_id but have
        # different cookies -> different keys. A cannot read B's history.
        store_a = _ENGINE.SessionStore()
        store_b = _ENGINE.SessionStore()
        key_a = derive_conversation_key(_request("shared-id", store_a))
        key_b = derive_conversation_key(_request("shared-id", store_b))
        self.assertNotEqual(key_a, key_b)

    def test_same_cookie_keeps_continuity(self):
        # Same SessionStore (same signed cookie) across two turns -> same key.
        store = _ENGINE.SessionStore()
        first = derive_conversation_key(_request(None, store))
        second = derive_conversation_key(_request(None, store))
        self.assertEqual(first, second)

    def test_caller_session_id_is_namespaced_under_cookie_root(self):
        store = _ENGINE.SessionStore()
        root = derive_conversation_key(_request(None, store))
        namespaced = derive_conversation_key(_request("sub-1", store))
        self.assertEqual(namespaced, f"{root}:sub-1")
        self.assertTrue(namespaced.startswith(root + ":"))

    def test_cross_session_clear_poison_blocked(self):
        # Attacker passes the victim's key as their own session_id; the derived
        # key is namespaced under the ATTACKER's cookie root, never the victim's.
        victim_store = _ENGINE.SessionStore()
        victim_key = derive_conversation_key(_request(None, victim_store))
        attacker_store = _ENGINE.SessionStore()
        attacker_key = derive_conversation_key(_request(victim_key, attacker_store))
        self.assertNotEqual(attacker_key, victim_key)

    def test_anonymous_keys_differ_across_cookies(self):
        key_a = derive_conversation_key(_request(None, _ENGINE.SessionStore()))
        key_b = derive_conversation_key(_request(None, _ENGINE.SessionStore()))
        self.assertNotEqual(key_a, key_b)

    def test_key_persisted_in_session_payload_and_marks_modified(self):
        # conv_id lives in the payload (the signed cookie); assigning it marks
        # the session modified so SessionMiddleware emits the Set-Cookie.
        store = _ENGINE.SessionStore()
        key = derive_conversation_key(_request(None, store))
        self.assertEqual(store.get("conv_id"), key)
        self.assertTrue(store.modified)

    def test_signed_cookies_session_key_is_not_used(self):
        # signed_cookies: session_key stays None; the derived key must still be
        # a real, non-None id (the conv_id), proving session_key is not used.
        store = _ENGINE.SessionStore()
        self.assertIsNone(store.session_key)
        key = derive_conversation_key(_request(None, store))
        self.assertIsNotNone(key)
        self.assertEqual(key, store.get("conv_id"))

    def test_no_session_attached_does_not_crash(self):
        # RequestFactory request without SessionMiddleware has no .session.
        key = derive_conversation_key(RequestFactory().get("/api/chat/"))
        self.assertTrue(key)

    def test_caller_session_id_non_dict_json_body_returns_none(self):
        # bug_005: a POST body that is valid JSON but NOT an object (null, a
        # list, a number, a bool, a bare string) must not crash with
        # AttributeError on body_data.get(...). It simply yields no caller id.
        from datascraper.session_key import _caller_session_id
        for raw in (b"null", b"[]", b"123", b"true", b'"hi"'):
            req = RequestFactory().post(
                "/api/chat/", data=raw, content_type="application/json"
            )
            self.assertIsNone(_caller_session_id(req), raw)

    def test_non_dict_json_body_still_derives_a_key(self):
        # End-to-end: an anonymous POST with a non-object JSON body must not
        # raise (which would be an anonymous 500 on validate_claims) and must
        # still return a usable cookie-rooted key.
        for raw in (b"null", b"[]", b"123", b"true"):
            req = RequestFactory().post(
                "/api/chat/", data=raw, content_type="application/json"
            )
            self.assertTrue(derive_conversation_key(req), raw)


class TestViewSessionBinding(SimpleTestCase):
    def test_views_get_session_id_idor(self):
        from api import views
        store_a = _ENGINE.SessionStore()
        store_b = _ENGINE.SessionStore()
        key_a = views._get_session_id(_request("shared-id", store_a))
        key_b = views._get_session_id(_request("shared-id", store_b))
        self.assertNotEqual(key_a, key_b)

    def test_context_integration_get_session_id_idor(self):
        from datascraper.context_integration import ContextIntegration
        ci = ContextIntegration()
        store_a = _ENGINE.SessionStore()
        store_b = _ENGINE.SessionStore()
        key_a = ci._get_session_id(_request("shared-id", store_a))
        key_b = ci._get_session_id(_request("shared-id", store_b))
        self.assertNotEqual(key_a, key_b)

    def test_both_resolvers_agree_for_same_cookie(self):
        from api import views
        from datascraper.context_integration import ContextIntegration
        store = _ENGINE.SessionStore()
        views_key = views._get_session_id(_request("sub", store))
        ci_key = ContextIntegration()._get_session_id(_request("sub", store))
        self.assertEqual(views_key, ci_key)

    def test_has_axiom_claims_ignores_caller_session_id(self):
        # The endpoint must IGNORE ?session_id=<guess> and use the cookie-bound
        # key from _get_session_id (closing the IDOR on the claims surface).
        from api import views
        req = RequestFactory().get("/api/axioms/has_claims/?session_id=attacker-guess")
        with patch("api.views._get_session_id", return_value="cookie:bound") as m, \
                patch("api.views.get_claims", return_value=[]) as gc:
            resp = views.has_axiom_claims(req)
        m.assert_called_once_with(req)
        gc.assert_called_once_with("cookie:bound")
        self.assertEqual(json.loads(resp.content)["session_id"], "cookie:bound")

    def test_validate_claims_ignores_caller_session_id(self):
        from api import views
        body = json.dumps({"session_id": "attacker-guess"}).encode()
        req = RequestFactory().post(
            "/api/axioms/validate/", data=body, content_type="application/json"
        )
        with patch("api.views._get_session_id", return_value="cookie:bound") as m, \
                patch("axioms.validate_session", return_value={"ok": True}) as vs:
            resp = views.validate_claims(req)
        m.assert_called_once_with(req)
        vs.assert_called_once_with("cookie:bound")
        self.assertEqual(resp.status_code, 200)
