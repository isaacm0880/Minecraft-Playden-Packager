"""Shared, rate-limited throughput estimates for GUI and terminal transfers."""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Estimate:
    rate: float | None = None
    eta: float | None = None
    stalled: bool = False


def eta_text(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return 'estimating…'
    if seconds <= 0:
        return 'finishing…'
    rounded = max(5, int(math.ceil(seconds / 5)) * 5)
    if rounded < 60:
        return f'about {rounded}s'
    minutes = max(1, int(math.ceil(rounded / 60)))
    return f'about {minutes} min' if minutes < 60 else f'about {math.ceil(minutes / 60)} h'


class ThroughputEstimator:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started: float | None = None
        self.sampled = self.updated = self.last_data = 0.
        self.done = self.previous = 0
        self.total: int | None = None
        self.samples = 0
        self.rate: float | None = None
        self.display = Estimate()

    def update(self, done: int, total: int | None, now: float) -> Estimate:
        if self.started is None or done < self.done or total != self.total:
            self.reset()
            self.started = self.sampled = self.updated = self.last_data = now
            self.done = self.previous = done
            self.total = total
            return self.display
        if done > self.done:
            self.last_data = now
        self.done = done
        elapsed = now - self.sampled
        if elapsed >= .5:
            sample = max(0, done - self.previous) / elapsed
            weight = 1 - math.exp(-elapsed / 5.)
            self.rate = sample if self.rate is None else self.rate + weight * (sample - self.rate)
            self.samples += 1
            self.sampled, self.previous = now, done
        stalled = now - self.last_data >= 5 and (total is None or done < total)
        complete = total is not None and done >= total
        if now - self.updated >= 1 or complete or stalled != self.display.stalled:
            ready = self.samples >= 3 and now - self.started >= 2 and self.rate is not None and self.rate > 0
            eta = 0. if complete else max(0, total - done) / self.rate if ready and total is not None and self.rate is not None and not stalled else None
            self.display = Estimate(0. if stalled else self.rate, eta, stalled)
            self.updated = now
        return self.display
