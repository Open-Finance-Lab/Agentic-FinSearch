"""Shared helpers for the heartbeat test suites.

Not a test module (unittest discover only collects test*.py); imported by
test_news_heartbeat.py and test_news_signals.py, which both stub
urllib.request.urlopen for the Discord/LLM delivery paths and pin their
main() lock paths as ResourceWarning-free.
"""
import contextlib
import email.message
import gc
import io
import urllib.error
import warnings


class FakeResponse:
    """Stands in for urllib's HTTP response (context manager + read)."""

    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_429(retry_after="2"):
    headers = email.message.Message()
    headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://discord.com/api", 429, "rate limited", headers,
        io.BytesIO(b"slow down"))


@contextlib.contextmanager
def assert_no_resource_warnings(case):
    """Fail `case` if the body leaks a ResourceWarning. gc.collect() runs
    while the recorder is still active so unclosed handles finalize into
    the recording list, not the real filter chain."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
        gc.collect()
    case.assertEqual(
        [w for w in caught if issubclass(w.category, ResourceWarning)], [])
