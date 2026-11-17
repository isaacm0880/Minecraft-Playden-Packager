"""Rate-limited terminal rendering; independent of the GUI progress callback."""
import math
import re
import shutil
import sys
import time
from typing import Callable, TextIO

from .progress import Progress
from .transfer_timing import ThroughputEstimator, eta_text


def size(value: float) -> str:
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(value) < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024
    raise AssertionError('unreachable')


def duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return '--:--'
    seconds = max(0, int(seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


class TransferProgress:
    """One interactive line at <= 10 Hz; sparse stage records in redirected logs.

    Use as a context manager so failures and Ctrl-C always terminate the line.
    Rates are calculated from byte events rather than parsing display messages.
    """
    def __init__(self, stream: TextIO | None = None, *, interactive: bool | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.stream = stream if stream is not None else sys.stderr
        self.interactive = self.stream.isatty() if interactive is None else interactive
        self.clock = clock
        self.stage = ''
        self.last_render = float('-inf')
        self.started = clock()
        self.rate: float | None = None
        self.estimator = ThroughputEstimator()
        self.active = False
        self.previous_width = 0

    def __enter__(self) -> 'TransferProgress':
        return self

    def __exit__(self, *args: object) -> None:
        self.finish_line()

    def finish_line(self) -> None:
        if self.active:
            self.stream.write('\n')
            self.stream.flush()
            self.active = False
            self.previous_width = 0

    def __call__(self, event: Progress) -> None:
        now = self.clock()
        changed = event.stage != self.stage
        done = event.bytes_copied if event.bytes_copied is not None else event.completed
        total = event.bytes_total if event.bytes_total is not None else event.total
        if changed:
            self.finish_line()
            self.stage = event.stage
            self.started = now
            self.rate = None
            self.estimator = ThroughputEstimator()
        estimate = self.estimator.update(done, total, now)
        self.rate = estimate.rate
        final = event.stage == 'network-complete'
        if final and not changed:
            return
        boundary = event.stage == 'network-send' and total is not None and done >= total
        if not changed and not final and not boundary and now - self.last_render < (.1 if self.interactive else 30):
            return
        elapsed = max(0, now - self.started)
        message = re.sub(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]', '', event.message)
        if event.stage in ('network-send', 'network-verify'):
            percent = min(100., 100 * done / total) if total else 0.
            count = f'{size(done)}/{size(total)}' if total is not None else size(done)
            if event.stage == 'network-send':
                current = f'{size(self.rate)}/s' if self.rate is not None else '--/s'
                average = f'{size(done / elapsed)}/s' if elapsed > 0 else '--/s'
                eta = estimate.eta
                message = (f'Transferring {percent:5.1f}% {count} {current} '
                           f'ETA {eta_text(eta)} avg {average} elapsed {duration(elapsed)}')
            else:
                message = f'Verifying {percent:5.1f}% {count}'
            if self.interactive:
                filled = int(percent / 10)
                message = '[' + '#' * filled + '-' * (10 - filled) + '] ' + message
        if self.interactive and '\n' in message:
            self.finish_line()
            self.stream.write(message + '\n')
            self.stream.flush()
            self.last_render = now
            return
        if self.interactive:
            width = max(1, shutil.get_terminal_size((120, 24)).columns - 1)
            message = message[:width]
            self.stream.write('\r' + message + ' ' * max(0, min(width, self.previous_width) - len(message)))
            self.previous_width = len(message)
            self.active = True
        else:
            self.stream.write(message + '\n')
        self.stream.flush()
        self.last_render = now
        if final:
            self.finish_line()
