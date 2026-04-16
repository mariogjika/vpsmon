import time


class DeltaCalculator:
    """Calculates per-second rates from cumulative counters."""

    def __init__(self):
        self._prev = {}
        self._prev_ts = {}

    def rate(self, key: str, current_value: float) -> float | None:
        now = time.time()
        prev = self._prev.get(key)
        prev_ts = self._prev_ts.get(key)
        self._prev[key] = current_value
        self._prev_ts[key] = now
        if prev is None or prev_ts is None:
            return None
        dt = now - prev_ts
        if dt <= 0:
            return 0.0
        delta = current_value - prev
        if delta < 0:
            return 0.0
        return delta / dt


class RingBuffer:
    """Fixed-size ring buffer for log lines."""

    def __init__(self, maxsize: int = 1000):
        self._buf = []
        self._maxsize = maxsize

    def append(self, item):
        self._buf.append(item)
        if len(self._buf) > self._maxsize:
            self._buf = self._buf[-self._maxsize:]

    def get_all(self) -> list:
        return list(self._buf)

    def get_recent(self, n: int) -> list:
        return list(self._buf[-n:])

    def __len__(self):
        return len(self._buf)
