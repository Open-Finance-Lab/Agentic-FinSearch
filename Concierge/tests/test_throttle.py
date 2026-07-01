from concierge.throttle import EditThrottle


def test_no_flush_when_empty():
    t = EditThrottle(interval_s=1.2, min_chars=1500)
    assert t.should_flush(0, now_s=100.0) is False


def test_flush_after_interval():
    t = EditThrottle(interval_s=1.2, min_chars=1500)
    assert t.should_flush(10, now_s=0.0) is True          # first content, >interval since 0.0
    t.mark_flushed(10, now_s=0.0)
    assert t.should_flush(20, now_s=0.5) is False          # too soon, too few chars
    assert t.should_flush(20, now_s=1.5) is True           # interval elapsed


def test_flush_after_min_chars():
    t = EditThrottle(interval_s=1.2, min_chars=1500)
    t.mark_flushed(10, now_s=0.0)
    assert t.should_flush(1600, now_s=0.1) is True         # +1590 chars >= min_chars
