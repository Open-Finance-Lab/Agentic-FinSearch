"""Static guard: the gunicorn config disables the unused control socket.

gunicorn 25.1's control_socket defaults to ./gunicorn.ctl in WORKDIR /app, which is
root-owned; as the non-root runtime user (uid 1001) that path is unwritable and the
arbiter logs a recurring "Failed to start control socket: Permission denied" warning on
every start and reload. We don't use the gunicornc management CLI, so it is disabled.
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
