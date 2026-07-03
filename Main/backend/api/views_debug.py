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
    """Verify the debug token from the X-Debug-Token header.

    Header only, deliberately: ?token= was dropped because query strings are
    recorded verbatim by the Caddy edge access log (and gunicorn's, before its
    format was redacted), so accepting the credential there turned every
    authenticated call into a plaintext token write to the log pipeline.
    """
    configured_token = os.environ.get('DEBUG_MEMORY_TOKEN', '')
    if not configured_token:
        return False
    return request.headers.get('X-Debug-Token', '') == configured_token


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
