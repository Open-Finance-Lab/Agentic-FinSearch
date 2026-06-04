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
