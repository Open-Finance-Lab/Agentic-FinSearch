"""Django-level tests for the /api/axioms/* HTTP surface.

Covers the contracts the API exposes to the Chrome extension and any
external client: validation method-gate + payload shape, and the
filename whitelist / path-traversal guard on the XBRL download view.
"""
import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_config.settings')

import django  # noqa: E402
from django.apps import apps as _django_apps  # noqa: E402

if not _django_apps.ready:
    django.setup()

from django.test import RequestFactory  # noqa: E402

from api import views  # noqa: E402


@pytest.fixture
def factory():
    return RequestFactory()


# ── /api/axioms/validate/ ─────────────────────────────────────────────


class TestValidateClaimsContract:
    """Request-shape contract for the validate endpoint. The actual
    verdict logic is tested by test_axiom_integration; here we lock the
    HTTP behavior so the Chrome extension can rely on it."""

    def test_get_method_returns_405(self, factory):
        req = factory.get('/api/axioms/validate/')
        resp = views.validate_claims(req)
        assert resp.status_code == 405

    def test_put_method_returns_405(self, factory):
        req = factory.put(
            '/api/axioms/validate/',
            data=b'{"session_id":"x"}',
            content_type='application/json',
        )
        resp = views.validate_claims(req)
        assert resp.status_code == 405

    def test_malformed_json_returns_400(self, factory):
        req = factory.post(
            '/api/axioms/validate/',
            data=b'not-json',
            content_type='application/json',
        )
        resp = views.validate_claims(req)
        assert resp.status_code == 400
        assert b'invalid JSON body' in resp.content

    def test_missing_session_id_returns_400_when_no_request_session(self, factory):
        # Body is valid JSON but carries no session_id and the request has
        # no Django session attached, so the view must reject with 400 —
        # not silently fall through to validate the empty default.
        req = factory.post(
            '/api/axioms/validate/',
            data=b'{}',
            content_type='application/json',
        )
        # _get_session_id touches request.session — RequestFactory does not
        # run middleware, so attribute access raises. The view must surface
        # 400 (session_id required) rather than crash with 500.
        with patch('api.views._get_session_id', return_value=None):
            resp = views.validate_claims(req)
        assert resp.status_code == 400
        assert b'session_id required' in resp.content

    def test_happy_path_returns_validate_session_payload(self, factory):
        body = json.dumps({'session_id': 'sess-test'}).encode()
        req = factory.post(
            '/api/axioms/validate/',
            data=body,
            content_type='application/json',
        )
        fake_payload = {
            'session_id': 'sess-test',
            'claims': [],
            'summary': {'total': 0, 'VERIFIED': 0, 'FAILED': 0,
                        'SKIPPED': 0, 'NOT_APPLICABLE': 0, 'ERROR': 0},
        }
        with patch('axioms.validate_session', return_value=fake_payload):
            resp = views.validate_claims(req)
        assert resp.status_code == 200
        assert json.loads(resp.content) == fake_payload


# ── /api/axioms/xbrl/<filename>/ ──────────────────────────────────────


class TestXbrlFilingDownloadGuards:
    """Filename whitelist + parent-containment check is the only thing
    standing between an unauthenticated GET and arbitrary file reads.
    Each attack shape gets its own test so a regression names itself."""

    @pytest.mark.parametrize('bad_name', [
        '../etc/passwd',
        '..%2Fetc%2Fpasswd',
        'aapl-20230930.xml/../secret',
        'aapl_20230930.xml',                # underscore not allowed
        'aapl-202309.xml',                  # too few date digits
        'aapl-20230930.txt',                # wrong extension
        'AAPL-20230930.xml',                # uppercase ticker not allowed
        'aapl-20230930.xml?x=1',            # query-string injected
        '',
    ])
    def test_invalid_filenames_return_404(self, factory, bad_name):
        req = factory.get(f'/api/axioms/xbrl/{bad_name}/')
        resp = views.xbrl_filing_download(req, bad_name)
        assert resp.status_code == 404

    def test_pattern_match_but_missing_file_returns_404(self, factory):
        # Whitelist passes but the file does not exist on disk.
        name = 'zzzz-20990101.xml'
        req = factory.get(f'/api/axioms/xbrl/{name}/')
        resp = views.xbrl_filing_download(req, name)
        assert resp.status_code == 404

    def test_existing_filing_is_served_inline(self, factory):
        # AAPL filing is committed under mcp_server/xbrl/filings/. If the
        # demo set ever changes the test should be updated to whichever
        # fixture is canonical.
        name = 'aapl-20230930.xml'
        req = factory.get(f'/api/axioms/xbrl/{name}/')
        resp = views.xbrl_filing_download(req, name)
        # FileResponse on success; HttpResponseNotFound otherwise. If the
        # filing was removed, document the expected name in the failure.
        assert resp.status_code == 200, (
            f'expected {name} to exist under mcp_server/xbrl/filings/'
        )
        assert resp['Content-Type'].startswith('application/xml')
        assert 'inline' in resp['Content-Disposition']
        # Drain so the FileResponse closes its handle.
        b''.join(resp.streaming_content)


# ── /api/axioms/has_claims/ ───────────────────────────────────────────


class TestHasAxiomClaims:
    def test_session_with_no_claims_returns_false(self, factory):
        req = factory.get('/api/axioms/has_claims/?session_id=empty-sess')
        with patch('api.views.get_claims', return_value=[]):
            resp = views.has_axiom_claims(req)
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data['has_claims'] is False
        assert data['count'] == 0

    def test_session_with_claims_returns_true(self, factory):
        req = factory.get('/api/axioms/has_claims/?session_id=full-sess')
        with patch('api.views.get_claims', return_value=[{'ratio': 'gross_margin'}]):
            resp = views.has_axiom_claims(req)
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data['has_claims'] is True
        assert data['count'] == 1
