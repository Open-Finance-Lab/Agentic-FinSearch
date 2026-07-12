"""Tests for fail-closed Bearer auth on /v1/* (api/openai_views.py)."""
import os
from unittest.mock import patch

from django.test import SimpleTestCase, RequestFactory, override_settings
from django.http import JsonResponse

from api.openai_views import _authenticate_request
from api.auth import authenticate_request, require_bearer_auth


class AuthFailClosedTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    @patch.dict(os.environ, {"FINGPT_API_KEY": "test-secret-key"})
    def test_missing_bearer_returns_401(self):
        response = _authenticate_request(self.rf.get("/v1/models"))
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 401)

    @patch.dict(os.environ, {"FINGPT_API_KEY": "test-secret-key"})
    def test_correct_bearer_passes(self):
        request = self.rf.get("/v1/models", HTTP_AUTHORIZATION="Bearer test-secret-key")
        self.assertIsNone(_authenticate_request(request))

    @override_settings(REQUIRE_FINGPT_API_KEY=True)
    def test_fail_closed_when_key_unset_and_required(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FINGPT_API_KEY", None)
            response = _authenticate_request(self.rf.get("/v1/models"))
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 503)

    @override_settings(REQUIRE_FINGPT_API_KEY=False)
    def test_dev_mode_when_key_unset_and_not_required(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FINGPT_API_KEY", None)
            self.assertIsNone(_authenticate_request(self.rf.get("/v1/models")))


# Decorator tests (module-level pytest functions)
@require_bearer_auth
def _probe(request):
    return JsonResponse({"ok": True})


@override_settings(REQUIRE_FINGPT_API_KEY=False)
def test_decorator_open_when_no_key(monkeypatch):
    monkeypatch.delenv("FINGPT_API_KEY", raising=False)
    resp = _probe(RequestFactory().get("/x/"))
    assert resp.status_code == 200


def test_decorator_401_when_key_set_and_header_missing(monkeypatch):
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    resp = _probe(RequestFactory().get("/x/"))
    assert resp.status_code == 401


def test_decorator_200_when_bearer_matches(monkeypatch):
    monkeypatch.setenv("FINGPT_API_KEY", "sekret")
    resp = _probe(RequestFactory().get("/x/", HTTP_AUTHORIZATION="Bearer sekret"))
    assert resp.status_code == 200


def test_decorator_503_when_required_and_no_key(monkeypatch):
    # Prod fail-closed (spec req 3): REQUIRE_FINGPT_API_KEY=True + no key -> 503, never silent-open.
    monkeypatch.delenv("FINGPT_API_KEY", raising=False)
    with override_settings(REQUIRE_FINGPT_API_KEY=True):
        resp = _probe(RequestFactory().get("/x/"))
    assert resp.status_code == 503
