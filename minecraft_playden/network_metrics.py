"""Opt-in, bounded numeric transfer diagnostics. Never record secrets or paths."""
import time
from collections.abc import Callable

MetricsCallback = Callable[[dict[str, int | float]], None]


class NetworkMetrics:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.values: dict[str, int | float] = {}

    def tick(self) -> float:
        return time.perf_counter() if self.enabled else 0.

    def add(self, name: str, value: int | float = 1) -> None:
        if self.enabled:
            self.values[name] = self.values.get(name, 0) + value

    def elapsed(self, name: str, start: float) -> None:
        if self.enabled:
            self.add(name, time.perf_counter() - start)
