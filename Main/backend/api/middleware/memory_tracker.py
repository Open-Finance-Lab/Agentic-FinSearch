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

            # Clear request context
            clear_request_context()
