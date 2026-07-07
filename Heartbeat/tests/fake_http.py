"""Shared fake urllib HTTP plumbing for the heartbeat test suites.

Not a test module (unittest discover only collects test*.py); imported by
test_news_heartbeat.py and test_news_signals.py, which both stub
urllib.request.urlopen for the Discord/LLM delivery paths.
"""
import email.message
import io
import urllib.error


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
