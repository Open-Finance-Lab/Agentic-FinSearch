"""Tests for the /debug/memory/ diagnostic endpoint."""
import pytest
import json
import os


# ── Token auth tests ──────────────────────────────────────────────

def test_missing_token_returns_403(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/')
    response = debug_memory(request)
    assert response.status_code == 403


def test_wrong_token_returns_403(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/', HTTP_X_DEBUG_TOKEN='wrong')
    response = debug_memory(request)
    assert response.status_code == 403


def test_correct_token_returns_200(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?action=status', HTTP_X_DEBUG_TOKEN='secret123')
    response = debug_memory(request)
    assert response.status_code == 200


def test_empty_token_config_disables_endpoint(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', '')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?action=status', HTTP_X_DEBUG_TOKEN='anything')
    response = debug_memory(request)
    assert response.status_code == 403


# ── Token transport: header only, never the query string ─────────

def test_correct_token_in_query_param_is_rejected(monkeypatch):
    # ?token= acceptance was removed deliberately: the query string is written verbatim
    # into the Caddy edge access log (and gunicorn's, before its format was redacted), so
    # that transport turned every authenticated call into a plaintext credential write.
    # Even the CORRECT token must be refused when it arrives in the URL.
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?token=secret123&action=status')
    response = debug_memory(request)
    assert response.status_code == 403


def test_header_token_accepted_and_query_param_ignored(monkeypatch):
    # The header path must keep working, and a stale/garbage ?token= alongside it must be
    # inert — auth decisions read only X-Debug-Token, never the (logged) query string.
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get(
        '/debug/memory/?token=wrong-and-ignored&action=status',
        HTTP_X_DEBUG_TOKEN='secret123',
    )
    response = debug_memory(request)
    assert response.status_code == 200


# ── Action: status ────────────────────────────────────────────────

def test_status_action_returns_snapshot(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_config.settings')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?action=status', HTTP_X_DEBUG_TOKEN='secret123')
    response = debug_memory(request)
    data = json.loads(response.content)
    assert 'snapshot' in data
    assert 'leak_detector' in data
    assert 'memory_mb' in data['snapshot']
    assert 'uss_mb' in data['snapshot']
    assert 'gc_counts' in data['snapshot']


# ── Action: snapshot (tracemalloc) ────────────────────────────────

def test_snapshot_action_starts_tracemalloc(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    import tracemalloc
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?action=snapshot', HTTP_X_DEBUG_TOKEN='secret123')
    response = debug_memory(request)
    data = json.loads(response.content)
    assert 'top_allocations' in data
    assert tracemalloc.is_tracing()
    tracemalloc.stop()  # Cleanup


# ── Action: stop ──────────────────────────────────────────────────

def test_stop_action_stops_tracemalloc(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    import tracemalloc
    tracemalloc.start()
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?action=stop', HTTP_X_DEBUG_TOKEN='secret123')
    response = debug_memory(request)
    assert response.status_code == 200
    assert not tracemalloc.is_tracing()
