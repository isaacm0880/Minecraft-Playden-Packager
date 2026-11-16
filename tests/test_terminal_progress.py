import io
from unittest.mock import patch

import pytest

from minecraft_playden.progress import Progress
from minecraft_playden.terminal_progress import TransferProgress, duration, size


class Clock:
    now = 0.
    def __call__(self):
        return self.now


def event(stage='network-send', done=0, total=1000):
    return Progress(stage, stage, done, total, bytes_copied=done, bytes_total=total)


def test_interactive_progress_is_single_line_throttled_and_reports_rate_eta():
    stream, clock = io.StringIO(), Clock()
    with TransferProgress(stream, interactive=True, clock=clock) as report:
        report(event())
        for i in range(1, 1001):
            clock.now = i / 1000
            report(event(done=i))
        assert stream.getvalue().count('\r') <= 11
        assert '\n' not in stream.getvalue()
        assert 'ETA 00:00' in stream.getvalue()
        assert 'B/s' in stream.getvalue()
        assert 'avg ' in stream.getvalue() and 'elapsed ' in stream.getvalue()
        report(event('network-verify', 1000))
        report(event('network-complete', 1000))
    assert stream.getvalue().count('\n') == 3
    assert stream.getvalue().endswith('\n')


@pytest.mark.parametrize('failure', [ValueError, KeyboardInterrupt])
def test_exceptions_finish_the_line(failure):
    stream = io.StringIO()
    with pytest.raises(failure):
        with TransferProgress(stream, interactive=True) as report:
            report(event())
            raise failure()
    assert stream.getvalue().endswith('\n')


def test_redirected_output_has_no_carriage_returns_and_bounded_records():
    stream, clock = io.StringIO(), Clock()
    with TransferProgress(stream, interactive=False, clock=clock) as report:
        for stage in ('network-validate', 'network-connect', 'network-send', 'network-verify', 'network-complete'):
            for i in range(1000):
                clock.now += .001
                report(event(stage, i))
    assert '\r' not in stream.getvalue()
    assert len(stream.getvalue().splitlines()) == 5


def test_terminal_resize_and_unknown_or_zero_totals():
    stream = io.StringIO()
    with TransferProgress(stream, interactive=True) as report:
        with patch('shutil.get_terminal_size', return_value=__import__('os').terminal_size((20, 24))):
            report(event(total=None))
        with patch('shutil.get_terminal_size', return_value=__import__('os').terminal_size((10, 24))):
            report(event('network-verify', total=0))
    assert max(map(len, stream.getvalue().replace('\r', '').splitlines())) <= 19
    assert duration(None) == '--:--'
    assert duration(float('inf')) == '--:--'
    assert size(1024) == '1.0 KiB'
