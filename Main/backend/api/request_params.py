"""Dual-accept (GET+POST) parameter extraction for the chat surface (Tier 3, phase 1).

The chat views historically read every input from the query string. The frozen
POST contract mirrors those params as a flat JSON object whose values are all
strings, at the same URL paths. During the dual-accept window every param read
in those views goes through :func:`merged_params`, which overlays the JSON body
of a ``POST`` + ``application/json`` request on top of ``request.GET``:

- body value wins over a same-key query-string value;
- plain GET and POST-with-query-params keep working unchanged;
- a missing/empty body behaves exactly like plain GET;
- a syntactically invalid JSON body raises :class:`MalformedJSONBody` so the
  view can answer 400 (client bug surfaced loudly, never a 500);
- a syntactically valid body that is not a JSON object (``null``/``[]``/
  ``"str"``/``123``) and any non-string values inside an object are ignored
  (fall back to GET) — mirroring the tolerance of
  ``datascraper.session_key._caller_session_id``.

``session_id`` note: the views never read ``session_id`` from this mapping;
``datascraper.session_key.derive_conversation_key`` reads GET-then-POST-body on
its own (pre-existing behavior, unchanged by this module).
"""
import json

from django.http import HttpRequest


class MalformedJSONBody(ValueError):
    """POST body declared ``application/json`` but is not parseable JSON."""


def merged_params(request: HttpRequest) -> dict:
    """Return a plain dict of request params: query string + JSON body overlay.

    For anything other than ``POST`` + ``Content-Type: application/json`` this
    is just ``request.GET`` flattened (last value per key, matching
    ``QueryDict.get``). Raises :class:`MalformedJSONBody` on a syntactically
    invalid JSON body so callers can return 400.
    """
    params = dict(request.GET.items())  # QueryDict: last value wins, like .get()

    if request.method != 'POST' or (request.content_type or '').lower() != 'application/json':
        return params

    body = request.body
    if not body:
        return params

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise MalformedJSONBody('invalid JSON body') from exc

    if isinstance(data, dict):
        params.update(
            (key, value)
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        )
    return params
