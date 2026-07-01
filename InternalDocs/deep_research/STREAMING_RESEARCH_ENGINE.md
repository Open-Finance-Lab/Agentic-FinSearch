# Streaming-by-Phase Research Engine

## Overview

Converted the blocking `run_iterative_research()` call into an async generator (`run_iterative_research_streaming()`) that yields phase-by-phase status updates via SSE. Users now see real-time feedback during the 2-3 minute research execution instead of a blank loading state.

**Branch**: `fingpt_backend_dev`
**Date**: February 2026

---

## Architecture

### Streaming Protocol

The research engine communicates via a sentinel tuple convention through the existing `(text_chunk, entries)` yield protocol:

| Tuple Shape | Meaning |
|---|---|
| `(None, {"label": "...", "detail": "..."})` | Status event (phase transition) |
| `("text", [])` | Synthesis content token |
| `("", [source_dicts...])` | Source delivery |

The SSE view detects `text_chunk is None` and emits a status frame instead of a content frame. Non-research streams are unaffected.

### Generator Chain

```
research_engine.run_iterative_research_streaming()
    -> datascraper._research_stream()           (wraps + fallback logic)
        -> views.py event_stream()              (SSE serialization)
            -> frontend onStatus callback       (UI status card)
```

### Phase Labels

| Phase | Label | Detail |
|-------|-------|--------|
| Query analysis | "Analyzing query" | *(none)* |
| Plan formed | "Planning research" | "Identified N sub-questions" |
| Sub-question completion | "Researching" | truncated sub-question (80 chars + "...") |
| Gap detection | "Evaluating results" | "Checking completeness" |
| Follow-up research | "Follow-up research" | truncated follow-up (80 chars + "...") |
| Synthesis | "Synthesizing findings" | "Combining N results" |

---

## Files Modified

### `datascraper/research_engine.py`
- **`_call_synthesis_streaming()`** — New helper for streaming synthesis calls with temperature retry
- **`_call_planner()`** — Added temperature retry (gpt-5-mini rejects `temperature=0.0`)
- **`_call_synthesis()`** — Added temperature retry (gpt-5.2-chat-latest rejects `temperature=0.2`)
- **`Synthesizer._build_synthesis_messages()`** — DRY helper for message construction
- **`Synthesizer.synthesize_streaming()`** — Async generator yielding tokens with non-streaming fallback; explicit `stream.close()` in `finally` block
- **`_status()`** — Helper building `(None, {"label", "detail"})` sentinel tuples
- **`run_iterative_research_streaming()`** — Main async generator orchestrating all phases:
  - Parallel sub-question execution via `asyncio.wait(FIRST_COMPLETED)` with per-completion status
  - Source deduplication and delivery before synthesis
  - Token-by-token synthesis streaming
  - Simple query bypass (yields nothing, triggers fallback)

### `datascraper/datascraper.py`
- **`_research_stream()`** (lines ~840-919) — Async generator wrapping the research engine:
  - Passes through status events and sources
  - Tracks `content_started` flag (not `got_any`) to correctly distinguish status-only vs synthesis-producing runs
  - Falls through to single-search path (`_create_advanced_response_stream_async()`) if no synthesis content produced
  - If exception after content started, re-raises; otherwise falls through gracefully
- **`get_sources()`** (line 1248) — Fixed `_get_or_create_session` -> `_load_session` (method didn't exist on `UnifiedContextManager`)

### `api/views.py`
- **SSE loop** (lines ~586-620):
  - Added status event detection: `text_chunk is None and isinstance(entries, dict) and "label" in entries`
  - Added `isinstance(entries, list)` guard on source entries branch
  - Added `try/finally` with `stream_iter.aclose()` + `loop.shutdown_asyncgens()` for proper async generator cleanup

### `datascraper/models_config.py`
- **`validate_model_support()`** — Added reverse lookup by `model_name` field so resolved names like `"gpt-5.2-chat-latest"` are recognized (not just display names like `"FinSearch"`)

### `frontend/src/modules/handlers.js`
- Added 6 research phase labels to `STATUS_LABEL_REMAPPINGS` for user-friendly display

### `gunicorn.conf.py` + `Dockerfile`
- Timeout extended to 1200s (20 minutes) to accommodate deep research runs with 3 iterations

### `tests/test_research_engine.py`
- `test_streaming_simple_query_yields_nothing` — Bypass signal for simple queries
- `test_streaming_status_event_format` — Label/detail type validation
- `test_streaming_phases_in_order` — Phase ordering with parallel execution
- `test_streaming_sources_before_synthesis` — Sources delivered before synthesis text

---

## Bugs Encountered & Fixes

### 1. Temperature rejection by OpenAI models
**Symptom**: `400 Bad Request` on `_call_planner` (gpt-5-mini, temp=0.0) and `_call_synthesis`/`_call_synthesis_streaming` (gpt-5.2-chat-latest, temp=0.2).
**Fix**: Try/except that retries without the `temperature` parameter when error message contains "temperature". Logged at INFO level.

### 2. Fallthrough logic: `got_any` vs `content_started`
**Symptom**: Status events set `got_any=True`, preventing fallback to single-search when research engine produced no synthesis.
**Fix**: Replaced with `content_started` flag that only triggers on actual synthesis text chunks.

### 3. Gunicorn worker timeout
**Symptom**: Workers killed at 120s during deep research (5 sub-questions + 3 follow-ups per iteration x 3 iterations).
**Root cause**: Dockerfile CMD hardcoded `--timeout 120`, overriding `gunicorn.conf.py`.
**Fix**: Changed to `--timeout 1200` in both Dockerfile and gunicorn.conf.py.

### 4. `validate_model_support` failing for resolved model names
**Symptom**: `gemini-3-flash-preview` flagged as "MCP not supported" even though `FinSearch` config has `supports_mcp: True`.
**Fix**: Added reverse lookup by `model_name` field in `validate_model_support()`.

### 5. Source URL retrieval broken
**Symptom**: `'UnifiedContextManager' object has no attribute '_get_or_create_session'` on `/get_source_urls/` endpoint.
**Fix**: Changed to `_load_session()` which is the correct method (has get-or-create semantics internally).

### 6. Async generator cleanup warning
**Symptom**: `Task was destroyed but it is pending! coro=<async_generator_athrow>` on every research completion.
**Fix**: Added `loop.shutdown_asyncgens()` before `loop.close()` in views.py — this is what `asyncio.run()` does internally. Also added explicit `stream.close()` in `synthesize_streaming()` finally block.

---

## Test Results

All 21 tests pass:
- 13 existing research engine tests
- 4 new streaming tests
- 4 research config tests

---

## Performance

| Metric | Before | After |
|--------|--------|-------|
| User feedback during research | None (blank loading) | Phase-by-phase status updates |
| Sub-question execution | Sequential | Parallel (`asyncio.wait(FIRST_COMPLETED)`) |
| Typical research duration | 60-120s | 60-120s (same, but with visibility) |
| Sources returned | 10 (on synthesis failure) | 40-60+ (full research) |
| Max supported duration | 120s (worker timeout) | 1200s (20 min) |
