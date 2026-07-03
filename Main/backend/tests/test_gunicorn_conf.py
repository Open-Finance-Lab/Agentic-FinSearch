"""Static guards over gunicorn.conf.py: control socket stays off, access log stays redacted.

gunicorn 25.1's control_socket defaults to ./gunicorn.ctl in WORKDIR /app, which is
root-owned; as the non-root runtime user (uid 1001) that path is unwritable and the
arbiter logs a recurring "Failed to start control socket: Permission denied" warning on
every start and reload. We don't use the gunicornc management CLI, so it is disabled.

The access log format must never log the query string: credentials have been passed
there before (the old /api/debug/memory/?token=... pattern), and %(r)s — the raw
request line — would write them verbatim into stdout log shipping.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
GUNICORN_CONF = os.path.join(_HERE, "..", "gunicorn.conf.py")


def _load_gunicorn_conf():
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", GUNICORN_CONF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_control_socket_disabled():
    mod = _load_gunicorn_conf()
    assert mod.control_socket_disable is True


def test_control_socket_disable_is_a_real_gunicorn_setting():
    # The check above passes even if gunicorn never recognizes the name (a typo, or a
    # future rename/removal): gunicorn silently ignores unknown config attributes, so the
    # socket would stay enabled and the "Permission denied" warning would return while the
    # test above stayed green. Assert the name is an actual gunicorn setting so a version
    # bump that drops it fails loudly here instead of silently in production.
    from gunicorn.config import KNOWN_SETTINGS

    assert "control_socket_disable" in {s.name for s in KNOWN_SETTINGS}


def test_access_log_format_never_logs_the_query_string():
    # %(r)s is the raw request line — path *and* query string — and %(q)s is the query
    # string alone. Either one would write ?token=-style credentials (or any signed-URL
    # secret) verbatim into stdout, which log shipping then persists. Pin the redacted
    # replacement: method + query-stripped path + protocol, quoted, so the overall line
    # keeps the combined-log shape downstream parsers expect.
    mod = _load_gunicorn_conf()
    assert "%(r)s" not in mod.access_log_format
    assert "%(q)s" not in mod.access_log_format
    assert '"%(m)s %(U)s %(H)s"' in mod.access_log_format


# Exactly the non-dynamic atoms gunicorn's glogging.Logger.atoms() provides (the
# {header}i/{header}o/{env}e forms are unused in our format). Values mimic a request
# that smuggles a credential in the query string, so the render test below can prove
# the formatted line drops it.
_STUB_ATOMS = {
    "h": "203.0.113.7",
    "l": "-",
    "u": "-",
    "t": "[03/Jul/2026:00:00:00 +0000]",
    "r": "GET /api/debug/memory/?token=hunter2 HTTP/1.1",
    "s": "200",
    "m": "GET",
    "U": "/api/debug/memory/",
    "q": "token=hunter2",
    "H": "HTTP/1.1",
    "b": "512",
    "B": 512,
    "f": "-",
    "a": "curl/8.5.0",
    "T": 0,
    "D": 1234,
    "M": 1,
    "L": "0.001234",
    "p": "<12345>",
}


def test_access_log_format_renders_against_gunicorn_atoms():
    # Belt and braces, same rationale as the control-socket setting check above: gunicorn
    # renders the format with %-interpolation over a SafeAtoms dict that maps unknown
    # names to "-", so a typo'd atom would silently log dashes forever. Rendering against
    # a *plain* dict of the real atom names makes any typo raise KeyError here in CI.
    mod = _load_gunicorn_conf()
    rendered = mod.access_log_format % _STUB_ATOMS
    assert rendered == (
        '203.0.113.7 - - [03/Jul/2026:00:00:00 +0000] '
        '"GET /api/debug/memory/ HTTP/1.1" 200 512 "-" "curl/8.5.0" 1234'
    )
    # The credential present in the stub's request line/query string never reaches the log.
    assert "hunter2" not in rendered
