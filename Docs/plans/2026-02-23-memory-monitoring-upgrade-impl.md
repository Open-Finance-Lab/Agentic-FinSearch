# Memory Monitoring Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the primitive single-baseline memory leak detection with trend-based detection using sliding window linear regression, add on-demand tracemalloc diagnostics, and proactive OOM prevention.

**Architecture:** Layered approach — `ResourceSnapshot` (stateless, per-request) feeds into `LeakDetector` (stateful, per-worker singleton) via gunicorn `post_request` hook. A separate `/debug/memory/` endpoint provides on-demand tracemalloc snapshots for diagnosing what's leaking. Proactive self-kill prevents OOM crashes.

**Tech Stack:** Python stdlib (`gc`, `tracemalloc`, `collections.deque`, `signal`), `psutil` (existing dep), Django views, gunicorn server hooks.

**Test runner:** `cd Main/backend && uv run pytest tests/<test_file> -v`

---

### Task 1: Enhanced ResourceSnapshot — Tests

**Files:**
- Create: `Main/backend/tests/test_resource_snapshot_enhanced.py`

**Step 1: Write tests for USS and GC stats**

```python
"""Tests for enhanced ResourceSnapshot with USS and GC stats."""
import pytest


# ── USS tracking tests ────────────────────────────────────────────

def test_snapshot_has_uss_mb():
    from api.utils.resource_monitor import ResourceSnapshot
    snap = ResourceSnapshot()
    assert hasattr(snap, 'uss_mb')
    assert isinstance(snap.uss_mb, float)
    assert snap.uss_mb > 0


def test_uss_less_than_or_equal_rss():
    from api.utils.resource_monitor import ResourceSnapshot
    snap = ResourceSnapshot()
    assert snap.uss_mb <= snap.memory_mb


# ── GC stats tests ────────────────────────────────────────────────

def test_snapshot_has_gc_stats():
    from api.utils.resource_monitor import ResourceSnapshot
    snap = ResourceSnapshot()
    assert hasattr(snap, 'gc_counts')
    assert isinstance(snap.gc_counts, tuple)
    assert len(snap.gc_counts) == 3  # gen0, gen1, gen2


def test_snapshot_has_gc_uncollectable():
    from api.utils.resource_monitor import ResourceSnapshot
    snap = ResourceSnapshot()
    assert hasattr(snap, 'gc_uncollectable')
    assert isinstance(snap.gc_uncollectable, int)
    assert snap.gc_uncollectable >= 0


# ── delta includes new fields ─────────────────────────────────────

def test_delta_includes_uss():
    from api.utils.resource_monitor import ResourceSnapshot
    before = ResourceSnapshot()
    after = ResourceSnapshot()
    delta = after.delta(before)
    assert 'uss_delta_mb' in delta


def test_delta_includes_gc_uncollectable():
    from api.utils.resource_monitor import ResourceSnapshot
    before = ResourceSnapshot()
    after = ResourceSnapshot()
    delta = after.delta(before)
    assert 'gc_uncollectable_delta' in delta


# ── to_dict includes new fields ───────────────────────────────────

def test_to_dict_includes_new_fields():
    from api.utils.resource_monitor import ResourceSnapshot
    snap = ResourceSnapshot()
    d = snap.to_dict()
    assert 'uss_mb' in d
    assert 'gc_counts' in d
    assert 'gc_uncollectable' in d
```

**Step 2: Run tests to verify they fail**

Run: `cd Main/backend && uv run pytest tests/test_resource_snapshot_enhanced.py -v`
Expected: FAIL — `AttributeError: 'ResourceSnapshot' object has no attribute 'uss_mb'`

---

### Task 2: Enhanced ResourceSnapshot — Implementation

**Files:**
- Modify: `Main/backend/api/utils/resource_monitor.py`

**Step 1: Add `gc` import and USS/GC fields to `__init__`**

Add `import gc` to the imports at the top (after `import asyncio`).

In `ResourceSnapshot.__init__`, after `self.browser_processes = ...` (line 22), add:

```python
        self.uss_mb = self._get_uss_mb()
        self.gc_counts = gc.get_count()
        self.gc_uncollectable = self._get_gc_uncollectable()
```

**Step 2: Add `_get_uss_mb` method**

After the `_get_memory_mb` method (after line 31), add:

```python
    def _get_uss_mb(self) -> float:
        """Get unique set size in MB (memory that would be freed if process killed)."""
        try:
            process = psutil.Process(self.pid)
            return process.memory_full_info().uss / 1024 / 1024
        except Exception:
            return self.memory_mb  # Fall back to RSS
```

**Step 3: Add `_get_gc_uncollectable` method**

After the new `_get_uss_mb` method, add:

```python
    def _get_gc_uncollectable(self) -> int:
        """Get count of uncollectable objects across all GC generations."""
        try:
            return sum(s.get('uncollectable', 0) for s in gc.get_stats())
        except Exception:
            return 0
```

**Step 4: Update `delta` method**

In the `delta` method, add to the returned dict (after `'browser_delta'` line):

```python
            'uss_delta_mb': round(self.uss_mb - previous.uss_mb, 2),
            'gc_uncollectable_delta': self.gc_uncollectable - previous.gc_uncollectable,
```

**Step 5: Update `to_dict` method**

In the `to_dict` method, add to the returned dict (after `'browser_processes'` line):

```python
            'uss_mb': round(self.uss_mb, 2),
            'gc_counts': self.gc_counts,
            'gc_uncollectable': self.gc_uncollectable,
```

**Step 6: Run tests to verify they pass**

Run: `cd Main/backend && uv run pytest tests/test_resource_snapshot_enhanced.py -v`
Expected: All 8 tests PASS

**Step 7: Run existing tests to verify no regression**

Run: `cd Main/backend && uv run pytest tests/test_hallucination_mitigation.py tests/test_research_config.py tests/test_research_engine.py -v`
Expected: All existing tests PASS

**Step 8: Commit**

```bash
git add Main/backend/api/utils/resource_monitor.py Main/backend/tests/test_resource_snapshot_enhanced.py
git commit -m "feat: add USS and GC stats to ResourceSnapshot"
```

---

### Task 3: LeakDetector — Tests

**Files:**
- Create: `Main/backend/tests/test_leak_detector.py`

**Step 1: Write tests for LeakDetector**

```python
"""Tests for the sliding window leak detector."""
import pytest


# ── Linear regression math ────────────────────────────────────────

def test_compute_slope_positive():
    """Steadily increasing data should produce positive slope."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=10, check_interval=5, slope_threshold=0.1)
    for i in range(10):
        detector.record(rss_mb=100.0 + i * 1.0)  # +1MB per request
    slope = detector.compute_slope()
    assert slope is not None
    assert abs(slope - 1.0) < 0.01


def test_compute_slope_flat():
    """Flat data should produce near-zero slope."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=10, check_interval=5, slope_threshold=0.1)
    for i in range(10):
        detector.record(rss_mb=100.0)
    slope = detector.compute_slope()
    assert slope is not None
    assert abs(slope) < 0.01


def test_compute_slope_negative():
    """Decreasing data should produce negative slope."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=10, check_interval=5, slope_threshold=0.1)
    for i in range(10):
        detector.record(rss_mb=200.0 - i * 2.0)
    slope = detector.compute_slope()
    assert slope is not None
    assert slope < -1.0


def test_compute_slope_insufficient_data():
    """Too few samples should return None."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=100, check_interval=5, slope_threshold=0.1)
    detector.record(rss_mb=100.0)
    assert detector.compute_slope() is None


# ── Leak detection ────────────────────────────────────────────────

def test_detects_steady_leak():
    """A steady 0.5 MB/request leak should be detected."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=100, check_interval=50, slope_threshold=0.1)
    result = None
    for i in range(100):
        result = detector.record(rss_mb=100.0 + i * 0.5)
    assert result is not None
    assert result['status'] == 'LEAK_TREND_DETECTED'
    assert result['slope'] > 0.4


def test_no_false_alarm_on_flat():
    """Stable memory should not trigger leak detection."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=100, check_interval=50, slope_threshold=0.1)
    result = None
    for i in range(100):
        result = detector.record(rss_mb=100.0)
    assert result is None or result.get('status') != 'LEAK_TREND_DETECTED'


def test_no_false_alarm_on_spike_then_stable():
    """A single spike followed by stable memory should not trigger."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=50, check_interval=25, slope_threshold=0.1)
    # Spike: first 5 requests grow fast
    for i in range(5):
        detector.record(rss_mb=100.0 + i * 10.0)
    # Then stable for 45 requests
    result = None
    for i in range(45):
        result = detector.record(rss_mb=150.0)
    assert result is None or result.get('status') != 'LEAK_TREND_DETECTED'


# ── High water mark ───────────────────────────────────────────────

def test_high_water_mark():
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=10, check_interval=5, slope_threshold=0.1)
    detector.record(rss_mb=100.0)
    detector.record(rss_mb=250.0)
    detector.record(rss_mb=150.0)
    assert detector.high_water_mark == 250.0


def test_high_water_mark_starts_zero():
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=10, check_interval=5, slope_threshold=0.1)
    assert detector.high_water_mark == 0.0


# ── Proactive self-kill ───────────────────────────────────────────

def test_soft_limit_triggers_signal(monkeypatch):
    """Exceeding soft limit should attempt graceful restart."""
    import signal
    signals_sent = []
    monkeypatch.setattr('os.kill', lambda pid, sig: signals_sent.append((pid, sig)))
    monkeypatch.setattr('os.getppid', lambda: 12345)

    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(
        window_size=10, check_interval=5, slope_threshold=0.1,
        soft_limit_mb=200.0
    )
    result = detector.record(rss_mb=250.0)
    assert any(sig == signal.SIGHUP for _, sig in signals_sent)
    assert result is not None
    assert result['status'] == 'SOFT_LIMIT_EXCEEDED'


def test_below_soft_limit_no_signal(monkeypatch):
    """Below soft limit should not trigger signal."""
    signals_sent = []
    monkeypatch.setattr('os.kill', lambda pid, sig: signals_sent.append((pid, sig)))
    monkeypatch.setattr('os.getppid', lambda: 12345)

    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(
        window_size=10, check_interval=5, slope_threshold=0.1,
        soft_limit_mb=500.0
    )
    detector.record(rss_mb=100.0)
    assert len(signals_sent) == 0


def test_soft_limit_only_fires_once(monkeypatch):
    """Self-kill signal should only be sent once per worker lifetime."""
    signals_sent = []
    monkeypatch.setattr('os.kill', lambda pid, sig: signals_sent.append((pid, sig)))
    monkeypatch.setattr('os.getppid', lambda: 12345)

    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(
        window_size=10, check_interval=5, slope_threshold=0.1,
        soft_limit_mb=200.0
    )
    detector.record(rss_mb=250.0)
    detector.record(rss_mb=260.0)
    detector.record(rss_mb=270.0)
    assert len(signals_sent) == 1  # Only once


# ── get_state ─────────────────────────────────────────────────────

def test_get_state_returns_dict():
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=10, check_interval=5, slope_threshold=0.1)
    for i in range(10):
        detector.record(rss_mb=100.0 + i)
    state = detector.get_state()
    assert 'slope' in state
    assert 'high_water_mark' in state
    assert 'request_count' in state
    assert 'window_size' in state


# ── Singleton access ──────────────────────────────────────────────

def test_get_worker_detector_returns_same_instance():
    from api.utils.leak_detector import get_worker_detector
    d1 = get_worker_detector()
    d2 = get_worker_detector()
    assert d1 is d2
```

**Step 2: Run tests to verify they fail**

Run: `cd Main/backend && uv run pytest tests/test_leak_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.utils.leak_detector'`

---

### Task 4: LeakDetector — Implementation

**Files:**
- Create: `Main/backend/api/utils/leak_detector.py`

**Step 1: Implement LeakDetector**

```python
"""Sliding window memory leak detector with trend analysis and proactive OOM prevention."""

import os
import signal
import logging
import time
from collections import deque
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Module-level singleton
_worker_detector: Optional['LeakDetector'] = None


class LeakDetector:
    """
    Detects memory leaks by tracking RSS over a sliding window
    and computing linear regression slope.

    A sustained positive slope above the threshold indicates a leak.
    Also provides proactive self-kill when RSS exceeds a soft limit.
    """

    def __init__(
        self,
        window_size: int = 200,
        check_interval: int = 50,
        slope_threshold: float = 0.1,
        soft_limit_mb: float = 450.0,
    ):
        self.window_size = window_size
        self.check_interval = check_interval
        self.slope_threshold = slope_threshold
        self.soft_limit_mb = soft_limit_mb

        self._samples: deque = deque(maxlen=window_size)
        self._request_count: int = 0
        self._high_water_mark: float = 0.0
        self._soft_limit_fired: bool = False
        self._last_slope: Optional[float] = None

    @property
    def high_water_mark(self) -> float:
        return self._high_water_mark

    def record(self, rss_mb: float) -> Optional[Dict[str, Any]]:
        """
        Record a memory measurement after a request.

        Returns a dict with status if action is needed, None otherwise.
        Possible statuses: 'LEAK_TREND_DETECTED', 'SOFT_LIMIT_EXCEEDED'
        """
        self._request_count += 1
        self._samples.append((self._request_count, rss_mb))
        self._high_water_mark = max(self._high_water_mark, rss_mb)

        # Check soft limit (proactive self-kill)
        if rss_mb > self.soft_limit_mb and not self._soft_limit_fired:
            self._soft_limit_fired = True
            self._request_graceful_restart(rss_mb)
            return {
                'status': 'SOFT_LIMIT_EXCEEDED',
                'rss_mb': rss_mb,
                'soft_limit_mb': self.soft_limit_mb,
            }

        # Check for leak trend at intervals
        if (self._request_count % self.check_interval == 0
                and len(self._samples) >= self.check_interval):
            slope = self.compute_slope()
            self._last_slope = slope
            if slope is not None and slope > self.slope_threshold:
                logger.warning(
                    f"LEAK_TREND_DETECTED: slope={slope:.4f} MB/req "
                    f"over {len(self._samples)} samples, "
                    f"high_water={self._high_water_mark:.1f}MB"
                )
                return {
                    'status': 'LEAK_TREND_DETECTED',
                    'slope': slope,
                    'window_size': len(self._samples),
                    'high_water_mark': self._high_water_mark,
                }

        return None

    def compute_slope(self) -> Optional[float]:
        """
        Compute linear regression slope (MB per request) over the sliding window.

        Uses least squares: slope = (n*Σxy - Σx*Σy) / (n*Σx² - (Σx)²)
        Returns None if insufficient data (< check_interval samples).
        """
        n = len(self._samples)
        if n < self.check_interval:
            return None

        sum_x = 0.0
        sum_y = 0.0
        sum_xy = 0.0
        sum_x2 = 0.0

        for x, y in self._samples:
            sum_x += x
            sum_y += y
            sum_xy += x * y
            sum_x2 += x * x

        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            return 0.0

        return (n * sum_xy - sum_x * sum_y) / denominator

    def get_state(self) -> Dict[str, Any]:
        """Return current detector state for diagnostics."""
        return {
            'slope': self._last_slope,
            'high_water_mark': self._high_water_mark,
            'request_count': self._request_count,
            'window_size': len(self._samples),
            'window_capacity': self.window_size,
            'soft_limit_mb': self.soft_limit_mb,
            'soft_limit_fired': self._soft_limit_fired,
            'slope_threshold': self.slope_threshold,
        }

    def _request_graceful_restart(self, rss_mb: float):
        """Send SIGHUP to gunicorn master to gracefully restart this worker."""
        try:
            parent_pid = os.getppid()
            logger.warning(
                f"SOFT_LIMIT_EXCEEDED: RSS={rss_mb:.1f}MB > limit={self.soft_limit_mb}MB. "
                f"Sending SIGHUP to gunicorn master (pid={parent_pid})"
            )
            os.kill(parent_pid, signal.SIGHUP)
        except Exception as e:
            logger.error(f"Failed to send SIGHUP to gunicorn master: {e}")


def get_worker_detector() -> LeakDetector:
    """Get or create the per-worker LeakDetector singleton."""
    global _worker_detector
    if _worker_detector is None:
        _worker_detector = LeakDetector(
            window_size=int(os.environ.get('MEMORY_LEAK_WINDOW_SIZE', '200')),
            check_interval=int(os.environ.get('MEMORY_LEAK_CHECK_INTERVAL', '50')),
            slope_threshold=float(os.environ.get('MEMORY_LEAK_SLOPE_THRESHOLD', '0.1')),
            soft_limit_mb=float(os.environ.get('MEMORY_SOFT_LIMIT_MB', '450')),
        )
    return _worker_detector
```

**Step 2: Run tests to verify they pass**

Run: `cd Main/backend && uv run pytest tests/test_leak_detector.py -v`
Expected: All 14 tests PASS

**Step 3: Commit**

```bash
git add Main/backend/api/utils/leak_detector.py Main/backend/tests/test_leak_detector.py
git commit -m "feat: add sliding window leak detector with linear regression"
```

---

### Task 5: Enhanced Middleware — Integration

**Files:**
- Modify: `Main/backend/api/middleware/memory_tracker.py`

**Step 1: Update middleware to integrate LeakDetector**

Replace the full content of `memory_tracker.py` with:

```python
"""Memory tracking middleware for identifying resource leaks."""

import logging
import time
from django.http import HttpRequest, HttpResponse
from typing import Callable

from api.utils.request_context import generate_request_id, set_request_id, clear_request_context
from api.utils.resource_monitor import ResourceSnapshot, get_mcp_connection_count
from api.utils.leak_detector import get_worker_detector

logger = logging.getLogger(__name__)

MEMORY_SPIKE_THRESHOLD_MB = 10.0


class MemoryTrackerMiddleware:
    """
    Middleware that tracks memory and resource usage per request.

    Logs:
    - Request ID for correlation
    - Worker PID
    - Memory usage before/after request
    - Resource deltas (file descriptors, asyncio tasks, browser processes)
    - Warnings for per-request spikes (SPIKE_DETECTED)
    - Warnings for sustained leak trends (LEAK_TREND_DETECTED) via LeakDetector
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Generate and store request ID
        request_id = generate_request_id()
        set_request_id(request_id)
        request.request_id = request_id

        # Snapshot resources before request
        before = ResourceSnapshot()
        start_time = time.time()

        try:
            response = self.get_response(request)
            return response
        finally:
            # Snapshot resources after request
            after = ResourceSnapshot()
            duration_ms = (time.time() - start_time) * 1000
            delta = after.delta(before)

            # Get additional context
            mcp_conns = get_mcp_connection_count()
            method = request.method
            path = request.path

            # Build log message
            log_parts = [
                f"[{request_id}]",
                f"[pid-{before.pid}]",
                f"{method} {path}",
                f"duration={duration_ms:.0f}ms",
                f"memory={before.memory_mb:.0f}MB->{after.memory_mb:.0f}MB",
                f"delta={delta['memory_delta_mb']:+.1f}MB",
            ]

            # Add USS delta if significant
            if abs(delta.get('uss_delta_mb', 0)) > 1.0:
                log_parts.append(f"uss_delta={delta['uss_delta_mb']:+.1f}MB")

            # Add resource counts if non-zero
            if after.asyncio_tasks > 0:
                log_parts.append(f"tasks={after.asyncio_tasks}")
            if delta['task_delta'] != 0:
                log_parts.append(f"task_delta={delta['task_delta']:+d}")
            if mcp_conns > 0:
                log_parts.append(f"mcp_conns={mcp_conns}")
            if after.browser_processes > 0:
                log_parts.append(f"browsers={after.browser_processes}")
            if delta['browser_delta'] != 0:
                log_parts.append(f"browser_delta={delta['browser_delta']:+d}")
            if delta['fd_delta'] != 0:
                log_parts.append(f"fd_delta={delta['fd_delta']:+d}")
            if delta.get('gc_uncollectable_delta', 0) != 0:
                log_parts.append(f"gc_uncollectable_delta={delta['gc_uncollectable_delta']:+d}")

            log_message = " | ".join(log_parts)

            # Log with appropriate level
            if delta['memory_delta_mb'] > MEMORY_SPIKE_THRESHOLD_MB:
                logger.warning(f"{log_message} | SPIKE_DETECTED")
            elif delta['memory_delta_mb'] > 5.0:
                logger.info(f"{log_message} | HIGH_MEMORY_USAGE")
            else:
                logger.debug(log_message)

            # Feed LeakDetector (non-blocking, best-effort)
            try:
                detector = get_worker_detector()
                result = detector.record(rss_mb=after.memory_mb)
                if result and result['status'] == 'LEAK_TREND_DETECTED':
                    logger.warning(
                        f"[{request_id}] [pid-{before.pid}] "
                        f"LEAK_TREND_DETECTED: slope={result['slope']:.4f} MB/req "
                        f"over {result['window_size']} requests"
                    )
                elif result and result['status'] == 'SOFT_LIMIT_EXCEEDED':
                    logger.critical(
                        f"[{request_id}] [pid-{before.pid}] "
                        f"SOFT_LIMIT_EXCEEDED: RSS={result['rss_mb']:.1f}MB > "
                        f"limit={result['soft_limit_mb']:.0f}MB — requesting graceful restart"
                    )
            except Exception as e:
                logger.debug(f"LeakDetector error (non-fatal): {e}")

            # Clear request context
            clear_request_context()
```

**Step 2: Run all existing tests to verify no regression**

Run: `cd Main/backend && uv run pytest tests/ -v --ignore=tests/test_openai_api.py`
Expected: All tests PASS (the openai_api tests have pre-existing Django config issues)

**Step 3: Commit**

```bash
git add Main/backend/api/middleware/memory_tracker.py
git commit -m "feat: integrate LeakDetector into memory tracking middleware"
```

---

### Task 6: Debug Memory Endpoint — Tests

**Files:**
- Create: `Main/backend/tests/test_debug_memory_endpoint.py`

**Step 1: Write tests for the debug endpoint**

```python
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
    request = factory.get('/debug/memory/?token=wrong')
    response = debug_memory(request)
    assert response.status_code == 403


def test_correct_token_returns_200(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?token=secret123&action=status')
    response = debug_memory(request)
    assert response.status_code == 200


def test_empty_token_config_disables_endpoint(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', '')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?token=anything&action=status')
    response = debug_memory(request)
    assert response.status_code == 403


# ── Action: status ────────────────────────────────────────────────

def test_status_action_returns_snapshot(monkeypatch):
    monkeypatch.setenv('DEBUG_MEMORY_TOKEN', 'secret123')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_config.settings')
    from api.views_debug import debug_memory
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/debug/memory/?token=secret123&action=status')
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
    request = factory.get('/debug/memory/?token=secret123&action=snapshot')
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
    request = factory.get('/debug/memory/?token=secret123&action=stop')
    response = debug_memory(request)
    assert response.status_code == 200
    assert not tracemalloc.is_tracing()
```

**Step 2: Run tests to verify they fail**

Run: `cd Main/backend && DJANGO_SETTINGS_MODULE=django_config.settings uv run pytest tests/test_debug_memory_endpoint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.views_debug'`

---

### Task 7: Debug Memory Endpoint — Implementation

**Files:**
- Create: `Main/backend/api/views_debug.py`
- Modify: `Main/backend/django_config/urls.py`

**Step 1: Implement the debug endpoint**

```python
"""Debug endpoints for memory diagnostics. Token-authenticated."""

import gc
import os
import tracemalloc
import logging
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from api.utils.resource_monitor import ResourceSnapshot
from api.utils.leak_detector import get_worker_detector

logger = logging.getLogger(__name__)

# Module-level storage for tracemalloc snapshot diffing
_previous_snapshot = None


def _check_token(request: HttpRequest) -> bool:
    """Verify the debug token from query param or header."""
    configured_token = os.environ.get('DEBUG_MEMORY_TOKEN', '')
    if not configured_token:
        return False
    request_token = request.GET.get('token', '')
    if not request_token:
        request_token = request.headers.get('X-Debug-Token', '')
    return request_token == configured_token


@csrf_exempt
@require_GET
def debug_memory(request: HttpRequest) -> JsonResponse:
    """
    Debug memory diagnostic endpoint.

    Actions:
    - status: Current ResourceSnapshot + LeakDetector state
    - snapshot: Take tracemalloc snapshot, return top allocators
    - diff: Compare current snapshot to previous, show growth
    - stop: Stop tracemalloc
    """
    if not _check_token(request):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    action = request.GET.get('action', 'status')

    if action == 'status':
        return _action_status()
    elif action == 'snapshot':
        return _action_snapshot(request)
    elif action == 'diff':
        return _action_diff(request)
    elif action == 'stop':
        return _action_stop()
    else:
        return JsonResponse({'error': f'Unknown action: {action}'}, status=400)


def _action_status() -> JsonResponse:
    """Return current resource snapshot and leak detector state."""
    gc.collect()
    snap = ResourceSnapshot()
    detector = get_worker_detector()
    return JsonResponse({
        'snapshot': snap.to_dict(),
        'leak_detector': detector.get_state(),
        'gc_stats': gc.get_stats(),
        'tracemalloc_active': tracemalloc.is_tracing(),
    })


def _action_snapshot(request: HttpRequest) -> JsonResponse:
    """Take a tracemalloc snapshot and return top allocators."""
    global _previous_snapshot

    frames = int(os.environ.get('TRACEMALLOC_FRAMES', '25'))
    if not tracemalloc.is_tracing():
        tracemalloc.start(frames)

    gc.collect()
    snapshot = tracemalloc.take_snapshot()
    _previous_snapshot = snapshot

    # Filter out importlib and tracemalloc internals
    snapshot = snapshot.filter_traces((
        tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
        tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
        tracemalloc.Filter(False, tracemalloc.__file__),
    ))

    limit = int(request.GET.get('limit', '20'))
    top_stats = snapshot.statistics('lineno')[:limit]

    return JsonResponse({
        'top_allocations': [
            {
                'file': str(stat.traceback),
                'size_kb': round(stat.size / 1024, 1),
                'count': stat.count,
            }
            for stat in top_stats
        ],
        'tracemalloc_overhead_kb': round(tracemalloc.get_tracemalloc_memory() / 1024, 1),
        'total_allocated_mb': round(sum(s.size for s in snapshot.statistics('filename')) / 1024 / 1024, 1),
    })


def _action_diff(request: HttpRequest) -> JsonResponse:
    """Compare current snapshot to previous. The core leak-hunting tool."""
    global _previous_snapshot

    if not tracemalloc.is_tracing():
        return JsonResponse({'error': 'tracemalloc not active. Call ?action=snapshot first.'}, status=400)

    if _previous_snapshot is None:
        return JsonResponse({'error': 'No previous snapshot. Call ?action=snapshot first.'}, status=400)

    gc.collect()
    current = tracemalloc.take_snapshot()

    # Filter internals
    current_filtered = current.filter_traces((
        tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
        tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
        tracemalloc.Filter(False, tracemalloc.__file__),
    ))
    previous_filtered = _previous_snapshot.filter_traces((
        tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
        tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
        tracemalloc.Filter(False, tracemalloc.__file__),
    ))

    limit = int(request.GET.get('limit', '20'))
    diff_stats = current_filtered.compare_to(previous_filtered, 'lineno')[:limit]

    _previous_snapshot = current  # Update for next diff

    return JsonResponse({
        'growth': [
            {
                'file': str(stat.traceback),
                'size_diff_kb': round(stat.size_diff / 1024, 1),
                'size_kb': round(stat.size / 1024, 1),
                'count_diff': stat.count_diff,
                'count': stat.count,
            }
            for stat in diff_stats
        ],
    })


def _action_stop() -> JsonResponse:
    """Stop tracemalloc to remove profiling overhead."""
    global _previous_snapshot
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    _previous_snapshot = None
    return JsonResponse({'status': 'tracemalloc stopped'})
```

**Step 2: Add URL route**

In `Main/backend/django_config/urls.py`, add the import and route. After the `from api import openai_views` line (line 4), add:

```python
from api import views_debug
```

In the `urlpatterns` list, before the OpenAI-compatible API comment (before line 24), add:

```python
    # Debug/diagnostic endpoints
    path('debug/memory/', views_debug.debug_memory, name='debug_memory'),
```

**Step 3: Run tests to verify they pass**

Run: `cd Main/backend && DJANGO_SETTINGS_MODULE=django_config.settings uv run pytest tests/test_debug_memory_endpoint.py -v`
Expected: All 8 tests PASS

**Step 4: Commit**

```bash
git add Main/backend/api/views_debug.py Main/backend/django_config/urls.py Main/backend/tests/test_debug_memory_endpoint.py
git commit -m "feat: add /debug/memory/ endpoint with tracemalloc diagnostics"
```

---

### Task 8: Gunicorn post_request Hook

**Files:**
- Modify: `Main/backend/gunicorn.conf.py`

**Step 1: Add post_request hook**

Add the following at the end of `gunicorn.conf.py` (after `tmp_upload_dir = None`, line 32):

```python

# ── Memory monitoring hooks ───────────────────────────────────────

def post_request(worker, req, environ, resp):
    """
    Feed RSS measurement into the per-worker LeakDetector after every response.
    This is the primary data source for trend analysis — more reliable than
    middleware because it fires even on middleware errors.
    """
    try:
        import psutil
        rss_mb = psutil.Process().memory_info().rss / 1024 / 1024
        from api.utils.leak_detector import get_worker_detector
        detector = get_worker_detector()
        result = detector.record(rss_mb=rss_mb)
        if result:
            worker.log.warning(
                f"[gunicorn] {result['status']}: "
                f"pid={worker.pid} rss={rss_mb:.1f}MB "
                f"{result}"
            )
    except Exception:
        pass  # Never crash the request pipeline for monitoring
```

**Step 2: Update middleware to avoid double-feeding**

In `Main/backend/api/middleware/memory_tracker.py`, remove the LeakDetector feeding block. Replace the `# Feed LeakDetector (non-blocking, best-effort)` try/except block (the one added in Task 5) with:

```python
            # Note: LeakDetector is fed by gunicorn post_request hook
            # (more reliable — fires even on middleware errors).
            # Middleware only reads detector state for logging.
            try:
                detector = get_worker_detector()
                state = detector.get_state()
                if state['slope'] is not None and state['slope'] > detector.slope_threshold:
                    logger.warning(
                        f"[{request_id}] [pid-{before.pid}] "
                        f"LEAK_TREND: slope={state['slope']:.4f} MB/req "
                        f"over {state['window_size']} requests"
                    )
            except Exception:
                pass
```

**Step 3: Run all tests**

Run: `cd Main/backend && uv run pytest tests/ -v --ignore=tests/test_openai_api.py`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add Main/backend/gunicorn.conf.py Main/backend/api/middleware/memory_tracker.py
git commit -m "feat: add gunicorn post_request hook for cumulative leak detection"
```

---

### Task 9: Configuration — .env.example Update

**Files:**
- Modify: `Main/backend/.env.example`

**Step 1: Add memory monitoring config section**

At the end of `.env.example`, add:

```

# Memory Monitoring
# Sliding window size for leak detection (number of requests)
MEMORY_LEAK_WINDOW_SIZE=200
# Slope threshold in MB/request to trigger leak alert
MEMORY_LEAK_SLOPE_THRESHOLD=0.1
# Check for leak trend every N requests
MEMORY_LEAK_CHECK_INTERVAL=50
# Soft limit: trigger graceful worker restart above this (MB)
MEMORY_SOFT_LIMIT_MB=450
# Token for /debug/memory/ endpoint (empty = disabled)
DEBUG_MEMORY_TOKEN=
# Stack frames stored per allocation in tracemalloc
TRACEMALLOC_FRAMES=25
```

**Step 2: Commit**

```bash
git add Main/backend/.env.example
git commit -m "docs: add memory monitoring env vars to .env.example"
```

---

### Task 10: Final Verification

**Step 1: Run the complete test suite**

Run: `cd Main/backend && uv run pytest tests/ -v --ignore=tests/test_openai_api.py`
Expected: All tests PASS — including:
- `test_resource_snapshot_enhanced.py` (8 tests)
- `test_leak_detector.py` (14 tests)
- `test_debug_memory_endpoint.py` (8 tests)
- All pre-existing tests (research config, research engine, calculator, numerical validator, hallucination mitigation)

**Step 2: Verify all new files exist**

Check that these files exist:
- `Main/backend/api/utils/leak_detector.py`
- `Main/backend/api/views_debug.py`
- `Main/backend/tests/test_leak_detector.py`
- `Main/backend/tests/test_resource_snapshot_enhanced.py`
- `Main/backend/tests/test_debug_memory_endpoint.py`

**Step 3: Final commit with all plan docs**

```bash
git add Docs/plans/2026-02-23-memory-monitoring-upgrade-design.md Docs/plans/2026-02-23-memory-monitoring-upgrade-impl.md
git commit -m "docs: add memory monitoring upgrade design and implementation plan"
```
