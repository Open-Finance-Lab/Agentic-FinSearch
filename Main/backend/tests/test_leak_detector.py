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
    """A transient spike that returns to baseline should not trigger."""
    from api.utils.leak_detector import LeakDetector
    detector = LeakDetector(window_size=100, check_interval=50, slope_threshold=0.1)
    # Spike: first 5 requests grow fast
    for i in range(5):
        detector.record(rss_mb=100.0 + i * 10.0)
    # Then return to baseline for 95 requests
    result = None
    for i in range(95):
        result = detector.record(rss_mb=100.0)
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
