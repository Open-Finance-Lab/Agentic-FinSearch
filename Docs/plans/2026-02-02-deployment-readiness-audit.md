# Deployment Readiness Audit - Memory Leak Investigation

**Date**: 2026-02-02
**Status**: In Progress
**Priority**: Critical

## Problem Statement

Production system experiencing:
- Memory growth in gunicorn workers over time
- Worker crashes/OOM kills requiring manual restarts
- Low & bursty traffic pattern (1-10 concurrent users)
- Issues surface after idle periods followed by activity spikes

## Root Cause Analysis

### Primary Suspects (Priority Order)

1. **MCP Client Connections**
   - Global singleton maintains persistent connections
   - Connections may not close between requests
   - Each leak: connection handles + buffers

2. **Playwright Browser Instances**
   - Browser contexts/pages created per request
   - Async cleanup may fail on abnormal exits
   - Each leak: ~50-200MB memory

3. **Agent Event Loops**
   - Async context managers create event loops
   - Abnormal exits may leave loops dangling
   - Tasks accumulate over time

4. **Context Manager State**
   - UnifiedContextManager + Mem0 accumulate session data
     *(2026-07 note: Mem0 has since been removed entirely — PR #347; only UnifiedContextManager remains.)*
   - No database (signed cookies) = all state in worker memory
   - Metadata persists despite compression

5. **Gunicorn Configuration**
   - 2 workers, 120s timeout, no auto-restart
   - No `max-requests` limit
   - Workers run indefinitely, accumulating leaks

## Solution Approach

### Phase 1: Add Observability

**Goal**: Make memory leaks visible in logs

**Components**:
1. Memory tracking middleware - log memory delta per request
2. Resource monitor utility - count MCP connections, browsers, tasks
3. Request context tracking - correlation IDs, worker PIDs

**Deliverables**:
- `api/middleware/memory_tracker.py`
- `api/utils/resource_monitor.py`
- `api/utils/request_context.py`
- Updated logging configuration

### Phase 2: Leak Detection

**Goal**: Identify specific leak sources

**Tests**:
1. Audit MCP connection lifecycle
2. Verify Playwright cleanup on all code paths
3. Check Agent async context manager exits
4. Monitor Context Manager memory growth
5. Profile worker memory over request count

### Phase 3: Fixes & Hardening

**Goal**: Eliminate leaks and prevent recurrence

**Actions**:
1. Fix confirmed resource leaks
2. Add missing cleanup/exception handling
3. Configure gunicorn max-requests for worker recycling
4. Implement connection pool limits
5. Add memory-based circuit breakers

## Monitoring Strategy

### Current State
- Basic log observation only
- Manual restarts when issues occur
- No metrics or APM

### Immediate Addition (Phase 1)
- Memory tracking per request
- Resource count logging
- Structured logs with correlation IDs

### Future Enhancement
- New Relic APM integration (planned)
- Alerting on memory thresholds
- Automated worker restart policies

## Success Criteria

1. **Visibility**: Memory growth visible in logs within 24h of deployment
2. **Identification**: Leak sources identified within 1 week
3. **Remediation**: Worker uptime >7 days without OOM
4. **Prevention**: Automated worker recycling prevents indefinite accumulation

## Implementation Notes

- Keep logs clean and minimal (no emojis, icons)
- Focus on actionable metrics only
- Deploy incrementally to production
- Use low-overhead monitoring (no external deps for Phase 1)
