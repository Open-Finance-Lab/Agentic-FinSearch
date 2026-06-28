from dataclasses import dataclass


@dataclass
class EditThrottle:
    interval_s: float
    min_chars: int
    _last_flush_s: float = float("-inf")   # first content always clears the interval gate
    _last_len: int = 0

    def should_flush(self, accumulated_len: int, now_s: float) -> bool:
        if accumulated_len == 0:
            return False
        if now_s - self._last_flush_s >= self.interval_s:
            return True
        if accumulated_len - self._last_len >= self.min_chars:
            return True
        return False

    def mark_flushed(self, accumulated_len: int, now_s: float) -> None:
        self._last_flush_s = now_s
        self._last_len = accumulated_len
