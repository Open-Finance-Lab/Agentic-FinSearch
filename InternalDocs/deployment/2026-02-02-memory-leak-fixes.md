# Memory Leak Fixes & Deployment Updates

**Date:** 2026-02-02
**Status:** Ready for deployment

## Problem

Production workers experiencing memory growth and OOM kills requiring manual restarts.

## Root Causes Identified

1. **MCP Manager** - Exit stack not closed, leaked connections
2. **Agent lifecycle** - No cleanup in context manager
3. **Session accumulation** - Cleanup method never called
4. **Worker immortality** - No automatic recycling
5. **Zero observability** - No memory tracking

## Fixes Applied

### 1. MCP Manager Cleanup
**File:** `mcp_client/mcp_manager.py`

Added exit stack cleanup:
```python
async def cleanup(self):
    self._stop_event.set()
    await self.exit_stack.aclose()  # Added
    self.sessions.clear()
```

### 2. Agent Cleanup
**File:** `mcp_client/agent.py`

Added cleanup in finally block:
```python
finally:
    if hasattr(agent, 'close'):
        await agent.close()
```

### 3. Automatic Session Cleanup
**File:** `datascraper/unified_context_manager.py`

Periodic cleanup every 10 requests:
```python
def _get_or_create_session(self, session_id: str):
    self._request_count += 1
    if self._request_count % self._cleanup_interval == 0:
        self.cleanup_expired_sessions()
```

### 4. Worker Recycling
**Files:** `Procfile`, `Dockerfile`

Both now have:
```
--max-requests 1000 --max-requests-jitter 50
```

### 5. Memory Monitoring
**New files:**
- `api/middleware/memory_tracker.py` - Logs memory per request
- `api/utils/resource_monitor.py` - Tracks resources
- `api/utils/request_context.py` - Request ID correlation
- `api/utils/logging_filters.py` - Request ID in all logs

**Logs format:**
```
INFO [abc123] [pid-12345] POST /api/chat | duration=1234ms | memory=125MB->128MB | delta=+3.0MB
WARNING [def456] [pid-12345] POST /api/chat | delta=+16.0MB | LEAK_SUSPECTED
```

### 6. GitHub Actions CI/CD
**File:** `.github/workflows/backend-deploy.yml`

Added:
- Pre-build verification: `uv run python verify_deployment.py`
- Post-deploy health check: systemd status + health endpoint

**Critical fix:** Dockerfile was missing `--max-requests` parameters (only in Procfile).

## New Tools

**Verification:**
```bash
uv run python verify_deployment.py
```

**Monitoring:**
```bash
python monitor_memory.py -i 30 -t 500
```

## Deployment

```bash
# 1. Verify locally
uv run python verify_deployment.py

# 2. Deploy
git add .
git commit -m "fix: resolve memory leaks and add production monitoring"
git push origin main

# 3. Monitor after deployment
ssh deploy@agenticfinsearch.org
journalctl -u fingpt-api -f | grep -E "memory|LEAK"
```

## Expected Behavior

**Worker recycling:**
```
INFO Shutting down worker: 54321 (max requests reached)
INFO Booting worker with pid: 54322
```

**Memory tracking:**
```
INFO [req-id] [pid] POST /api/chat | memory=125MB->128MB | delta=+3.0MB
```

**Leak detection:**
```
WARNING [req-id] [pid] POST /api/chat | delta=+16.0MB | LEAK_SUSPECTED
```

## Success Metrics

| Metric | Before | Target |
|--------|--------|--------|
| Worker uptime | Hours (crashes) | Days (stable) |
| Memory growth | +50MB/hr | <5MB/hr |
| Worker restart | Manual daily | Auto ~1000 reqs |
| Leak visibility | None | Per-request logs |

## Files Changed

**Core fixes:**
- `mcp_client/mcp_manager.py`
- `mcp_client/agent.py`
- `datascraper/unified_context_manager.py`
- `Procfile`
- `Dockerfile`

**Monitoring:**
- `api/middleware/memory_tracker.py` (new)
- `api/utils/resource_monitor.py` (new)
- `api/utils/request_context.py` (new)
- `api/utils/logging_filters.py` (new)
- `django_config/settings.py` (updated logging)
- `django_config/settings_prod.py` (updated logging)

**Dependencies:**
- `pyproject.toml` (added psutil)

**CI/CD:**
- `.github/workflows/backend-deploy.yml`

**Tools:**
- `verify_deployment.py` (new)
- `monitor_memory.py` (new)

## Rollback

If issues occur:
```bash
git revert HEAD
git push origin main
```

Or disable monitoring middleware in `settings.py`.

## Notes

- Tests currently disabled in CI (`RUN_TESTS: "false"`)
- Enable when ready: Set `RUN_TESTS: "true"`
- New Relic integration planned for future
