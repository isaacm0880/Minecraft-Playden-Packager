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
        assert 'ETA finishing…' in stream.getvalue()
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


@pytest.mark.parametrize('redirect_stdout,no_color', [(True, False), (False, True)])
def test_cli_redirected_stdout_and_no_color_disable_interactive_display(tmp_path, monkeypatch, redirect_stdout, no_color):
    from minecraft_playden.cli import main
    from minecraft_playden.network_transfer import NetworkRequest, NetworkResult
    monkeypatch.setenv('PLAYDEN_PS5_TOKEN', 'ab' * 32)
    if no_color:
        monkeypatch.setenv('NO_COLOR', '')
    else:
        monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setattr('sys.argv', ['packager', 'transfer-ps5', str(tmp_path), '--host', '127.0.0.1'])
    output, error = io.StringIO(), io.StringIO()
    monkeypatch.setattr('sys.stdout', output)
    monkeypatch.setattr('sys.stderr', error)
    monkeypatch.setattr(output, 'isatty', lambda: not redirect_stdout)
    monkeypatch.setattr(error, 'isatty', lambda: True)
    def run(self, progress, **kwargs):
        progress(event())
        progress(event('network-complete', 1000))
        return NetworkResult(tmp_path, '/data/playden-import/Example', 1, 1000)
    with patch.object(NetworkRequest, 'run', run):
        assert main() == 0
    assert '\r' not in error.getvalue()
    assert 'received and verified' in output.getvalue()


def test_cli_diagnostic_failure_finishes_bar_before_counters_and_error(tmp_path, monkeypatch):
    from minecraft_playden.cli import main
    from minecraft_playden.network_transfer import NetworkRequest
    monkeypatch.setenv('PLAYDEN_PS5_TOKEN', 'ab' * 32)
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setattr('sys.argv', ['packager', 'transfer-ps5', str(tmp_path), '--host', '127.0.0.1', '--diagnostics'])
    output, error = io.StringIO(), io.StringIO()
    monkeypatch.setattr('sys.stdout', output)
    monkeypatch.setattr('sys.stderr', error)
    monkeypatch.setattr(output, 'isatty', lambda: True)
    monkeypatch.setattr(error, 'isatty', lambda: True)
    def run(self, progress, **kwargs):
        progress(event())
        kwargs['diagnostics']({'bytes': 100})
        raise OSError('Disconnected')
    with patch.object(NetworkRequest, 'run', run):
        assert main() == 1
    text = error.getvalue()
    assert '\nsender_metrics ' in text
    assert text.endswith('Error: Disconnected\n')
    assert 'ab' * 32 not in text



def test_resize_during_same_stage_does_not_wrap_old_line_padding():
    stream, clock = io.StringIO(), Clock()
    with TransferProgress(stream, interactive=True, clock=clock) as report:
        with patch('shutil.get_terminal_size', return_value=__import__('os').terminal_size((120, 24))):
            report(event(done=1))
        clock.now = .2
        with patch('shutil.get_terminal_size', return_value=__import__('os').terminal_size((40, 24))):
            report(event(done=2))
        assert len(stream.getvalue().split('\r')[-1]) <= 39


def test_final_transfer_bytes_render_even_inside_throttle_interval():
    stream, clock = io.StringIO(), Clock()
    with TransferProgress(stream, interactive=True, clock=clock) as report:
        report(event(done=1))
        clock.now = .01
        report(event(done=1000))
    assert '100.0%' in stream.getvalue()
