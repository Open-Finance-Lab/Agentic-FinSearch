# Memory Monitoring Upgrade Design

**Date:** 2026-02-23
**Status:** Approved
**Branch:** fingpt_backend_dev

## Problem

The current memory leak detection is primitive:
- `monitor_memory.py` compares RSS against a single first-seen baseline with static thresholds
- `memory_tracker.py` flags individual requests that spike >10MB but misses gradual leaks
- `resource_monitor.py` tracks only RSS (not USS), ignores GC stats entirely
- No on-demand diagnostic capability (tracemalloc, object introspection)
- No trend analysis, no growth rate detection, no proactive response

A slow leak of 0.5MB/request across 1000 requests (500MB total) would never trigger any alert.

## Architecture

Layered approach with separated concerns by lifecycle:

```
resource_monitor.py  (existing, enhanced)
  └── ResourceSnapshot: adds USS + GC stats         [per-request, stateless]

leak_detector.py  (new)
  └── LeakDetector: sliding window + regression      [per-worker, long-lived]
  └── Proactive self-kill logic                      [safety net]

memory_tracker.py  (existing, enhanced)
  └── Feeds measurements to LeakDetector             [middleware, glue layer]
  └── Per-request logging (spike detection)          [human-readable logs]

debug_views.py  (new)
  └── /debug/memory/ endpoint with tracemalloc       [on-demand diagnostic]

gunicorn.conf.py  (enhanced)
  └── post_request hook feeds LeakDetector            [cumulative tracking]
```

## Components

### 1. Enhanced ResourceSnapshot (resource_monitor.py)

Add to existing class:
- **USS**: `psutil.Process().memory_full_info().uss` — true per-worker memory cost without shared pages
- **GC stats**: `gc.get_stats()` for collection counts per generation, `gc.get_count()` for pending objects
- **GC uncollectable count**: rising gen2 uncollectables indicate reference cycle leaks

The `delta()` method gains corresponding fields. `memory_full_info()` is ~1ms vs ~0.1ms for `memory_info()` — acceptable per-request overhead.

### 2. LeakDetector (new: leak_detector.py)

**Data structure:** `deque(maxlen=window_size)` of `(request_number, rss_mb, timestamp)` tuples.

**Leak detection algorithm:**
1. After each request, append current RSS to the window
2. Every `check_interval` requests, compute linear regression slope
3. If slope > threshold sustained over the full window, emit `LEAK_TREND_DETECTED`
4. Track high-water mark (peak RSS for this worker's lifetime)

**Growth rate calculation:** Least-squares regression using stdlib math only (no numpy):
```
slope = (n*sum_xy - sum_x*sum_y) / (n*sum_x2 - sum_x**2)
```

**Proactive self-kill:** If RSS exceeds soft limit, send `SIGHUP` to gunicorn master for graceful worker restart. Last-resort safety net before OOM kill.

### 3. Enhanced Middleware (memory_tracker.py)

Changes:
- Feed measurements to the worker's LeakDetector singleton
- Log `LEAK_TREND_DETECTED` with slope when detector fires (separate from per-request spike)
- Rename existing >10MB alert from `LEAK_SUSPECTED` to `SPIKE_DETECTED` for clarity

**Division of responsibility:** Gunicorn hook is primary feed for LeakDetector (more reliable). Middleware handles per-request human-readable logging only.

### 4. Debug Memory Endpoint (new: debug_views.py)

`GET /debug/memory/?token=SECRET`

Auth: secret token via `DEBUG_MEMORY_TOKEN` env var. Empty = endpoint disabled.

Modes:
- `?action=status` — current ResourceSnapshot + LeakDetector state (slope, window, high-water mark)
- `?action=snapshot` — start tracemalloc if not running, take snapshot, return top 20 allocators
- `?action=diff` — compare current snapshot to previous, show growth by file:line
- `?action=stop` — stop tracemalloc, remove overhead

tracemalloc is NOT always-on. Only active between snapshot/diff calls.

### 5. Gunicorn post_request Hook (gunicorn.conf.py)

`post_request(worker, req, environ, resp)` fires after every response. More reliable than middleware — fires even on middleware errors.

Feeds RSS into LeakDetector. This is the primary data source for trend analysis.

## Configuration

All thresholds via env vars with defaults:

| Env Var | Default | Purpose |
|---------|---------|---------|
| `MEMORY_LEAK_WINDOW_SIZE` | `200` | Measurements in sliding window |
| `MEMORY_LEAK_SLOPE_THRESHOLD` | `0.1` | MB/request slope to trigger alert |
| `MEMORY_LEAK_CHECK_INTERVAL` | `50` | Evaluate regression every N requests |
| `MEMORY_SOFT_LIMIT_MB` | `450` | Graceful restart above this |
| `MEMORY_HARD_LIMIT_MB` | `512` | Reference limit (container limit) |
| `DEBUG_MEMORY_TOKEN` | `""` | Token for /debug/memory (empty = disabled) |
| `TRACEMALLOC_FRAMES` | `25` | Stack frames per allocation |

## Testing Strategy

- `tests/test_leak_detector.py` — regression math, threshold logic, proactive kill signaling, false positive prevention
- `tests/test_resource_snapshot_enhanced.py` — USS and GC stats capture
- `tests/test_debug_memory_endpoint.py` — token auth, snapshot/diff modes, tracemalloc lifecycle
- Integration tests: synthetic "leaking" data into LeakDetector to verify trend detection without false alarms on normal patterns

## Files Changed

**Enhanced:**
- `api/utils/resource_monitor.py` — add USS + GC stats to ResourceSnapshot
- `api/middleware/memory_tracker.py` — integrate LeakDetector, rename alerts
- `gunicorn.conf.py` — add post_request hook

**New:**
- `api/utils/leak_detector.py` — sliding window + regression + self-kill
- `api/views_debug.py` — /debug/memory endpoint
- `tests/test_leak_detector.py`
- `tests/test_resource_snapshot_enhanced.py`
- `tests/test_debug_memory_endpoint.py`

**Config:**
- `.env.example` — add new env vars
- `django_config/urls.py` — add debug endpoint route
