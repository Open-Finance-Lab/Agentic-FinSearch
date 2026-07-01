"""Tests for the OpenAI-compatible API (api/openai_views.py)."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(method='POST', body=None, headers=None):
    """Create a mock Django HttpRequest."""
    request = MagicMock()
    request.method = method
    request.body = json.dumps(body).encode() if body else b'{}'
    request.META = headers or {}
    request.GET = {}
    request.session = MagicMock()
    request.session.session_key = 'test_session_key'
    return request


def _minimal_body(**overrides):
    """Return the minimal valid request body, with optional overrides."""
    body = {
        "model": "FinGPT",
        "messages": [{"role": "user", "content": "What is AAPL price?"}],
        "mode": "thinking",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------

class TestAuthentication:
    """Test Bearer token authentication on /v1/ endpoints."""

    @patch.dict(os.environ, {"FINGPT_API_KEY": "test-secret-key"})
    def test_missing_auth_header_returns_401(self):
        from api.openai_views import chat_completions
        request = _make_request(body=_minimal_body())
        response = chat_completions(request)
        assert response.status_code == 401
        data = json.loads(response.content)
        assert "authentication_error" in str(data)

    @patch.dict(os.environ, {"FINGPT_API_KEY": "test-secret-key"})
    def test_invalid_api_key_returns_401(self):
        from api.openai_views import chat_completions
        request = _make_request(
            body=_minimal_body(),
            headers={"HTTP_AUTHORIZATION": "Bearer wrong-key"}
        )
        response = chat_completions(request)
        assert response.status_code == 401

    @patch.dict(os.environ, {"FINGPT_API_KEY": "test-secret-key"})
    def test_malformed_auth_header_returns_401(self):
        from api.openai_views import chat_completions
        request = _make_request(
            body=_minimal_body(),
            headers={"HTTP_AUTHORIZATION": "Basic dXNlcjpwYXNz"}
        )
        response = chat_completions(request)
        assert response.status_code == 401

    @patch.dict(os.environ, {}, clear=False)
    def test_no_api_key_configured_allows_all_requests(self):
        """When FINGPT_API_KEY is not set, auth is disabled (dev mode)."""
        from api.openai_views import _authenticate_request
        # Remove FINGPT_API_KEY if present
        os.environ.pop("FINGPT_API_KEY", None)
        request = _make_request()
        result = _authenticate_request(request)
        assert result is None  # No error = authenticated

    @patch.dict(os.environ, {"FINGPT_API_KEY": "test-secret-key"})
    def test_valid_api_key_passes_auth(self):
        from api.openai_views import _authenticate_request
        request = _make_request(headers={"HTTP_AUTHORIZATION": "Bearer test-secret-key"})
        result = _authenticate_request(request)
        assert result is None  # No error = authenticated

    @patch.dict(os.environ, {"FINGPT_API_KEY": "test-secret-key"})
    def test_models_list_requires_auth(self):
        from api.openai_views import models_list
        request = _make_request(method='GET')
        response = models_list(request)
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Mode validation tests
# ---------------------------------------------------------------------------

class TestModeValidation:
    """Test mode parameter validation."""

    def _call_with_mode(self, mode_str):
        from api.openai_views import chat_completions
        body = _minimal_body(mode=mode_str)
        request = _make_request(body=body)
        # Disable auth for validation tests
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FINGPT_API_KEY", None)
            return chat_completions(request)

    def test_missing_mode_returns_400(self):
        from api.openai_views import chat_completions
        body = _minimal_body()
        del body["mode"]
        request = _make_request(body=body)
        os.environ.pop("FINGPT_API_KEY", None)
        response = chat_completions(request)
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "mode is required" in data["error"]["message"]

    def test_invalid_mode_returns_400(self):
        response = self._call_with_mode("reserch")  # typo
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "Invalid mode" in data["error"]["message"]

    def test_empty_mode_returns_400(self):
        response = self._call_with_mode("")
        assert response.status_code == 400

    def test_valid_thinking_mode_accepted(self):
        """Thinking mode is valid (will fail at execution, but not at validation)."""
        # We only test validation here — execution is mocked in integration tests
        response = self._call_with_mode("thinking")
        # Should NOT be a 400 (might be 500 if execution env isn't set up, that's fine)
        assert response.status_code != 400

    def test_valid_research_mode_accepted(self):
        response = self._call_with_mode("research")
        assert response.status_code != 400


# ---------------------------------------------------------------------------
# Request validation tests
# ---------------------------------------------------------------------------

class TestRequestValidation:
    """Test general request validation."""

    def _call(self, body):
        from api.openai_views import chat_completions
        request = _make_request(body=body)
        os.environ.pop("FINGPT_API_KEY", None)
        return chat_completions(request)

    def test_empty_messages_returns_400(self):
        response = self._call(_minimal_body(messages=[]))
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "messages" in data["error"]["message"]

    def test_invalid_model_returns_404(self):
        response = self._call(_minimal_body(model="nonexistent-model"))
        assert response.status_code == 404

    def test_get_method_returns_405(self):
        from api.openai_views import chat_completions
        request = _make_request(method='GET', body=_minimal_body())
        os.environ.pop("FINGPT_API_KEY", None)
        response = chat_completions(request)
        assert response.status_code == 405

    def test_invalid_json_body_returns_400(self):
        from api.openai_views import chat_completions
        request = MagicMock()
        request.method = 'POST'
        request.body = b'not valid json'
        request.META = {}
        os.environ.pop("FINGPT_API_KEY", None)
        response = chat_completions(request)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Domain merging tests
# ---------------------------------------------------------------------------

class TestDomainMerging:
    """Test search_domains -> preferred_links merging."""

    def test_merge_domains_into_empty_links(self):
        from api.openai_views import _merge_domains_into_preferred_links
        result = _merge_domains_into_preferred_links([], ["reuters.com", "bloomberg.com"])
        expected = {"https://reuters.com", "https://bloomberg.com"}
        assert expected.issubset(set(result))

    def test_merge_domains_preserves_existing_links(self):
        from api.openai_views import _merge_domains_into_preferred_links
        existing = ["https://example.com"]
        result = _merge_domains_into_preferred_links(existing, ["reuters.com"])
        expected = {"https://example.com", "https://reuters.com"}
        assert expected.issubset(set(result))

    def test_merge_domains_deduplicates(self):
        from api.openai_views import _merge_domains_into_preferred_links
        result = _merge_domains_into_preferred_links(
            ["https://reuters.com"],
            ["reuters.com"]  # same domain
        )
        assert result.count("https://reuters.com") == 1

    def test_merge_handles_full_urls(self):
        from api.openai_views import _merge_domains_into_preferred_links
        result = _merge_domains_into_preferred_links([], ["https://reuters.com/markets"])
        assert any(x == "https://reuters.com/markets" for x in result)

    def test_merge_handles_none_domains(self):
        from api.openai_views import _merge_domains_into_preferred_links
        result = _merge_domains_into_preferred_links(["https://x.com"], None)
        assert result == ["https://x.com"]

    def test_merge_skips_empty_strings(self):
        from api.openai_views import _merge_domains_into_preferred_links
        result = _merge_domains_into_preferred_links([], ["reuters.com", "", "  "])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Sync response format tests
# ---------------------------------------------------------------------------

class TestSyncResponseFormat:
    """Test that sync responses include correct fields and sources."""

    @patch("datascraper.datascraper.create_advanced_response")
    @patch("datascraper.datascraper.create_agent_response")
    def test_research_mode_returns_sources(self, mock_agent, mock_research):
        """Research mode sync response must include sources."""
        mock_sources = [
            {"url": "https://reuters.com/article", "title": "Reuters Article"},
            {"url": "https://bloomberg.com/news", "title": "Bloomberg News"},
        ]
        mock_research.return_value = ("AAPL is up 5% today.", mock_sources)

        from api.openai_views import _handle_sync

        context_mgr = MagicMock()
        integration = MagicMock()
        meta = MagicMock()
        meta.mode = MagicMock()  # Will be overridden by mode param
        meta.current_url = ""
        meta.user_timezone = None
        meta.user_time = None
        context_mgr.get_session_metadata.return_value = meta
        context_mgr.get_session_stats.return_value = {"token_count": 100}

        from datascraper.unified_context_manager import ContextMode
        response = _handle_sync(
            context_mgr, integration, "test_session",
            "What is AAPL price?", [], "FinGPT",
            ContextMode.RESEARCH, ["https://reuters.com"]
        )

        data = json.loads(response.content)
        assert "sources" in data
        assert len(data["sources"]) == 2
        assert data["sources"][0]["url"] == "https://reuters.com/article"
        assert data["choices"][0]["message"]["content"] == "AAPL is up 5% today."

    @patch("datascraper.datascraper.create_agent_response")
    def test_thinking_mode_returns_tool_sources(self, mock_agent):
        """Thinking mode sync response must include tool sources."""
        mock_tool_sources = [
            {"type": "tool", "tool_name": "get_stock_info", "symbol": "AAPL"},
        ]
        mock_agent.return_value = ("Apple is trading at $195.50.", mock_tool_sources)

        from api.openai_views import _handle_sync
        from datascraper.unified_context_manager import ContextMode

        context_mgr = MagicMock()
        integration = MagicMock()
        meta = MagicMock()
        meta.current_url = "https://finance.yahoo.com"
        meta.user_timezone = None
        meta.user_time = None
        context_mgr.get_session_metadata.return_value = meta
        context_mgr.get_session_stats.return_value = {"token_count": 50}

        response = _handle_sync(
            context_mgr, integration, "test_session",
            "What is AAPL price?", [], "FinGPT",
            ContextMode.THINKING
        )

        data = json.loads(response.content)
        assert "sources" in data
        assert len(data["sources"]) == 1
        assert data["sources"][0]["tool_name"] == "get_stock_info"
        assert data["sources"][0]["symbol"] == "AAPL"

    @patch("datascraper.datascraper.create_agent_response")
    def test_response_has_openai_standard_fields(self, mock_agent):
        """Response must include standard OpenAI fields."""
        mock_agent.return_value = ("Response text.", [])

        from api.openai_views import _handle_sync
        from datascraper.unified_context_manager import ContextMode

        context_mgr = MagicMock()
        integration = MagicMock()
        meta = MagicMock()
        meta.current_url = ""
        meta.user_timezone = None
        meta.user_time = None
        context_mgr.get_session_metadata.return_value = meta
        context_mgr.get_session_stats.return_value = {"token_count": 30}

        response = _handle_sync(
            context_mgr, integration, "test_session",
            "Hello", [], "FinGPT",
            ContextMode.THINKING
        )

        data = json.loads(response.content)
        assert data["object"] == "chat.completion"
        assert data["id"].startswith("chatcmpl-")
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "usage" in data
        assert "prompt_tokens" in data["usage"]


# ---------------------------------------------------------------------------
# Tool source extraction tests
# ---------------------------------------------------------------------------

class TestToolSourceExtraction:
    """Test _extract_tool_sources_from_result from datascraper.

    The real ``RunResult`` exposes tool calls via ``raw_responses[*].output``,
    where each function-call item is an ``openai.types.responses`` object whose
    ``.type`` is the literal ``"function_call"`` (matching the agents SDK's
    ``ResponseFunctionToolCall``). There is NO RunItem with type
    ``"function_call_item"``; the previous version of these tests mocked
    ``new_items`` with that fictional type and never exercised the real code
    path. The helpers below build the correct SDK shape.
    """

    @staticmethod
    def _function_call(name, call_id, **args):
        item = MagicMock()
        item.type = "function_call"
        item.name = name
        item.call_id = call_id
        item.arguments = json.dumps(args)
        return item

    @classmethod
    def _result(cls, output_items):
        """Build a RunResult-like mock: raw_responses[0].output = output_items."""
        resp = MagicMock()
        resp.output = output_items
        result = MagicMock()
        result.raw_responses = [resp]
        return result

    def test_extracts_function_calls(self):
        from datascraper.datascraper import _extract_tool_sources_from_result

        result = self._result([self._function_call("get_stock_info", "call_123", symbol="AAPL")])

        sources = _extract_tool_sources_from_result(result)
        assert len(sources) == 1
        assert sources[0]["tool_name"] == "get_stock_info"
        assert sources[0]["symbol"] == "AAPL"

    def test_deduplicates_tool_names(self):
        from datascraper.datascraper import _extract_tool_sources_from_result

        # Same call_id repeated → deduplicated to a single source.
        items = [self._function_call("get_stock_info", "call_dup", symbol="AAPL") for _ in range(3)]
        result = self._result(items)

        sources = _extract_tool_sources_from_result(result)
        assert len(sources) == 1  # Deduplicated by call_id

    def test_handles_empty_result(self):
        from datascraper.datascraper import _extract_tool_sources_from_result

        result = self._result([])
        sources = _extract_tool_sources_from_result(result)
        assert sources == []

    def test_handles_missing_new_items(self):
        from datascraper.datascraper import _extract_tool_sources_from_result

        mock_result = MagicMock(spec=[])  # No raw_responses attribute
        sources = _extract_tool_sources_from_result(mock_result)
        assert sources == []

    def test_extracts_multiple_tools(self):
        from datascraper.datascraper import _extract_tool_sources_from_result

        item1 = self._function_call("get_stock_info", "call_1", symbol="AAPL")
        item2 = self._function_call("get_stock_history", "call_2", symbol="MSFT", period="1mo")

        # A non-function item that should be ignored.
        item3 = MagicMock()
        item3.type = "tool_call_output_item"
        item3.output = "some output"

        result = self._result([item1, item2, item3])

        sources = _extract_tool_sources_from_result(result)
        assert len(sources) == 2
        tool_names = {s["tool_name"] for s in sources}
        assert tool_names == {"get_stock_info", "get_stock_history"}


# ---------------------------------------------------------------------------
# Agent budget enforcement tests (P0 Root-C.3 — /v1 must not bypass the cap)
# ---------------------------------------------------------------------------

class TestBudgetEnforcement:
    """/v1/chat/completions sync handler must honor agent_run_slot: a slot
    rejection (concurrency or daily budget) returns an OpenAI-style 503 with a
    Retry-After header, NOT a 500 and NOT an unbounded agent run."""

    def _rejecting_slot(self, exc):
        """Drop-in for agent_run_slot whose __enter__ raises."""
        cm = MagicMock()
        cm.__enter__.side_effect = exc
        return MagicMock(return_value=cm)

    def _sync_args(self):
        from datascraper.unified_context_manager import ContextMode
        context_mgr = MagicMock()
        integration = MagicMock()
        meta = MagicMock()
        meta.current_url = ""
        meta.user_timezone = None
        meta.user_time = None
        context_mgr.get_session_metadata.return_value = meta
        context_mgr.get_session_stats.return_value = {"token_count": 10}
        return context_mgr, integration, ContextMode.THINKING

    def test_sync_handler_returns_503_on_concurrency(self):
        from api.agent_budget import ConcurrencyExceeded
        from api.openai_views import _handle_sync

        context_mgr, integration, mode = self._sync_args()
        with patch("api.openai_views.agent_run_slot",
                   self._rejecting_slot(ConcurrencyExceeded())), \
             patch("api.openai_views.get_request_identity", return_value="ip:1.2.3.4"), \
             patch("datascraper.datascraper.create_agent_response") as mock_agent:
            response = _handle_sync(
                context_mgr, integration, "test_session",
                "What is AAPL price?", [], "FinGPT", mode
            )

        assert response.status_code == 503
        assert response["Retry-After"] == "30"
        data = json.loads(response.content)
        # OpenAI-style error shape (matches the other /v1 errors).
        assert "error" in data
        assert "message" in data["error"]
        assert "type" in data["error"]
        # The agent must NOT have been driven when the slot is rejected.
        mock_agent.assert_not_called()

    def test_sync_handler_returns_503_on_budget_exceeded(self):
        from api.agent_budget import BudgetExceeded
        from api.openai_views import _handle_sync

        context_mgr, integration, mode = self._sync_args()
        with patch("api.openai_views.agent_run_slot",
                   self._rejecting_slot(BudgetExceeded())), \
             patch("api.openai_views.get_request_identity", return_value="ip:1.2.3.4"), \
             patch("datascraper.datascraper.create_agent_response"):
            response = _handle_sync(
                context_mgr, integration, "test_session",
                "What is AAPL price?", [], "FinGPT", mode
            )

        assert response.status_code == 503
        assert response["Retry-After"] == "30"


# ---------------------------------------------------------------------------
# Models list tests
# ---------------------------------------------------------------------------

class TestModelsList:
    """Test GET /v1/models."""

    def test_returns_models_in_openai_format(self):
        from api.openai_views import models_list
        os.environ.pop("FINGPT_API_KEY", None)
        request = _make_request(method='GET')
        response = models_list(request)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["object"] == "list"
        assert len(data["data"]) > 0
        for model in data["data"]:
            assert "id" in model
            assert model["object"] == "model"
            assert "owned_by" in model

    def test_post_method_not_allowed(self):
        from api.openai_views import models_list
        os.environ.pop("FINGPT_API_KEY", None)
        request = _make_request(method='POST')
        response = models_list(request)
        assert response.status_code == 405
