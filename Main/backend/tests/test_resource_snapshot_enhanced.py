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

def test_snapshot_has_gc_counts():
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
