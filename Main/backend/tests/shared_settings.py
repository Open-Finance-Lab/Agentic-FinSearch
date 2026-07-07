"""Shared Django settings snippets for the backend test suite.

Deliberately NOT in tests/conftest.py: `import conftest` resolves to
Main/backend/conftest.py instead, because the tests conftest forces the
backend dir to the front of sys.path (see its docstring).
"""

# Hermeticity knobs for suites that drive a REAL request through the full
# middleware stack under bare pytest (no Django test runner / pytest-django):
# setup_test_environment() never runs, so 'testserver' is not auto-added to
# ALLOWED_HOSTS and CommonMiddleware raises DisallowedHost before URL
# resolution. CI also runs with no .env (DJANGO_DEBUG unset -> False), which
# defaults SECURE_SSL_REDIRECT True and would 301 the plain-HTTP test-client
# request before it reaches the view (the 301-trap, PR #313).
HERMETIC_REQUEST_SETTINGS = {
    "ALLOWED_HOSTS": ["testserver"],
    "SECURE_SSL_REDIRECT": False,
}
