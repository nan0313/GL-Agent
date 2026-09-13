from dataclasses import dataclass
from time import monotonic
from threading import RLock


@dataclass
class CircuitSnapshot:
    state: str = "closed"
    failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._items: dict[str, CircuitSnapshot] = {}
        self._lock = RLock()

    def state(self, purpose: str) -> str:
        with self._lock:
            item = self._items.setdefault(purpose, CircuitSnapshot())
            if item.state == "open" and item.opened_at is not None and monotonic() - item.opened_at >= self.cooldown_seconds:
                item.state = "half_open"
            return item.state

    def allow(self, purpose: str) -> bool:
        with self._lock:
            item = self._items.setdefault(purpose, CircuitSnapshot())
            state = self.state(purpose)
            if state == "open":
                return False
            if state == "half_open":
                if item.probe_in_flight:
                    return False
                item.probe_in_flight = True
            return True

    def record_success(self, purpose: str) -> None:
        with self._lock:
            self._items[purpose] = CircuitSnapshot(state="closed")

    def record_failure(self, purpose: str) -> None:
        with self._lock:
            item = self._items.setdefault(purpose, CircuitSnapshot())
            item.failures += 1
            if item.failures >= self.failure_threshold:
                item.state = "open"
                item.opened_at = monotonic()
                item.probe_in_flight = False

    def snapshot(self, purpose: str) -> CircuitSnapshot:
        state = self.state(purpose)
        with self._lock:
            item = self._items.setdefault(purpose, CircuitSnapshot())
            return CircuitSnapshot(state=state, failures=item.failures, opened_at=item.opened_at, probe_in_flight=item.probe_in_flight)
