"""TTL cache for Yahoo Finance data."""

import threading
from datetime import datetime, timedelta
from typing import Any, Optional


class TimedCache:
    """Thread-safe TTL cache with max-size eviction for yfinance Ticker objects.

    Eviction strategy:
    1. Expired entries are removed on every get() and set().
    2. If the cache exceeds max_entries after inserting, the oldest
       entries (by insertion time) are evicted until within bounds.
    """

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 50):
        """Initialize cache.

        Args:
            ttl_seconds: Time to live in seconds (default: 5 minutes)
            max_entries: Maximum number of entries before LRU eviction
        """
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_entries = max_entries
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._lock = threading.Lock()

    def _evict_expired(self, now: datetime) -> int:
        """Remove expired entries. Returns count evicted. Caller must hold lock."""
        expired = [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]
        for k in expired:
            del self._cache[k]
        return len(expired)

    def _evict_oldest(self) -> int:
        """Remove oldest entries until at max_entries. Caller must hold lock."""
        if len(self._cache) <= self.max_entries:
            return 0
        # Sort by timestamp (oldest first) and remove excess
        sorted_keys = sorted(self._cache, key=lambda k: self._cache[k][1])
        to_remove = len(self._cache) - self.max_entries
        for k in sorted_keys[:to_remove]:
            del self._cache[k]
        return to_remove

    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value if exists and not expired, None otherwise
        """
        with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if datetime.now() - timestamp < self.ttl:
                    return value
                # Expired, remove it
                del self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        """Cache value with current timestamp.

        Evicts expired entries first, then enforces max_entries via
        oldest-first eviction if the cache is still over capacity.

        Args:
            key: Cache key
            value: Value to cache
        """
        now = datetime.now()
        with self._lock:
            self._evict_expired(now)
            self._cache[key] = (value, now)
            self._evict_oldest()

    def clear(self) -> None:
        """Clear all cached values."""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        """Return number of entries (including potentially expired ones)."""
        with self._lock:
            return len(self._cache)
