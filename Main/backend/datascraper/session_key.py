"""Cookie-bound conversation-key derivation (P1 C-session / IDOR fix).

The conversation/history cache key MUST be rooted in a stable per-browser id
that lives inside the SIGNED session cookie, so a caller cannot read or poison
another caller's history by guessing their ``session_id``. Any caller-supplied
``session_id`` is namespaced UNDER the cookie root (``root:sub``) -- it selects a
sub-conversation within the caller's own cookie and never crosses to another
browser. This keeps the Concierge/extension request contract (callers may still
send ``session_id``) while closing the IDOR.

signed_cookies gotcha: ``request.session.session_key`` is ``None`` right after
``create()`` for a first-time visitor and otherwise equals the signed
serialization of the session contents (content-dependent, unstable). So we do
NOT use ``session_key`` as the key; we store our own uuid4 (``conv_id``) inside
the session payload -- assigning it marks the session modified, which makes
SessionMiddleware emit the ``fingpt_sessionid`` cookie on the response.
"""
import json
import logging
import uuid
from typing import Optional

from django.http import HttpRequest

logger = logging.getLogger(__name__)

CONV_ID_KEY = "conv_id"


def _caller_session_id(request: HttpRequest) -> Optional[str]:
    """Read a caller-supplied ``session_id`` from GET then the POST JSON body.

    This value is NEVER trusted as the cache key on its own; it is used only as
    a sub-namespace under the cookie root.
    """
    custom = request.GET.get("session_id")
    if not custom and request.method == "POST":
        try:
            body_data = json.loads(request.body)
            # A body of ``null``/``[]``/``123``/``true``/``"str"`` is valid JSON
            # but not an object; ``.get`` on it would raise AttributeError
            # (outside the except tuple) -> anonymous 500. Only dicts carry a
            # caller-supplied session_id.
            custom = body_data.get("session_id") if isinstance(body_data, dict) else None
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            pass
    return custom or None


def _cookie_root(request: HttpRequest) -> str:
    """Return a stable per-browser id stored inside the signed-cookie session.

    Falls back to a fresh uuid when no Django session is attached (e.g. a
    RequestFactory request without SessionMiddleware) so callers never crash.
    """
    session = getattr(request, "session", None)
    if session is None:
        return uuid.uuid4().hex

    if not session.session_key:
        session.create()

    root = session.get(CONV_ID_KEY)
    if not root:
        root = uuid.uuid4().hex
        session[CONV_ID_KEY] = root  # marks session modified -> Set-Cookie

    return root


def derive_conversation_key(request: HttpRequest) -> str:
    """Derive the conversation/history cache key bound to the signed cookie.

    Returns ``root`` for an anonymous caller, or ``root:<caller_session_id>``
    when the caller supplies one. The caller-supplied id is namespaced under the
    cookie root, so caller A (root_a) can never reach caller B's key (root_b:*).
    """
    root = _cookie_root(request)
    custom = _caller_session_id(request)
    if custom:
        return f"{root}:{custom}"
    return root
