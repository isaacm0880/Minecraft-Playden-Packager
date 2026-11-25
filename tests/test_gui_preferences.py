"""Offline GUI preferences are per-user display settings, independent of the CLI."""
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from minecraft_playden.cli import main
from minecraft_playden.desktop import offline_player_name
from minecraft_playden.discovery import DiscoveryResult, Installation

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from PySide6.QtWidgets import QApplication
    from minecraft_playden.gui_app import MainWindow


@pytest.mark.parametrize(('value', 'expected'), [
    (None, 'Player'), (42, 'Player'), ([], 'Player'), ('', 'Player'), ('  ', 'Player'),
    ('\x00\n\r\t\ud800', 'Player'), ('  Preview Player  ', 'Preview Player'),
    ('A\x00B\nC', 'ABC'), ('Zoë 玩家', 'Zoë 玩家'), ('Name "quoted"', 'Name "quoted"'),
])
def test_display_name_normalisation_preserves_supported_offline_names(value, expected):
    assert offline_player_name(value) == expected


@pytest.fixture
def preferences(tmp_path):
    if not HAS_QT:
        pytest.skip('Install .[gui] for Qt preferences tests')
    app = QApplication.instance() or QApplication([])
    path = str(tmp_path / 'preferences.ini')
    windows = []
    def create():
        # A fresh settings object on every launch; never touch native user settings.
        window = MainWindow(tmp_path / 'cache', [])
        windows.append(window)
        return window
    with patch('minecraft_playden.gui_app.QSettings', side_effect=lambda *args: QSettings(path, QSettings.Format.IniFormat)), patch('minecraft_playden.gui_app.QTimer.singleShot'):
        yield create, lambda: QSettings(path, QSettings.Format.IniFormat)
    for window in windows:
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_first_run_and_repeated_launches_remember_changes_immediately(preferences):
    create, read_settings = preferences
    first = create()
    assert first.username.text() == 'Player'
    assert first.ps5_destination.currentData() == 'internal'
    assert first.usb_candidates() == []
    assert not first.copy_usb_button.isEnabled()
    assert not first.retry_usb_button.isEnabled()
    first.username.setText('Zoë Preview Player')
    assert read_settings().value('player/offlineName') == 'Zoë Preview Player'
    first.ps5_destination.setCurrentIndex(1)
    assert read_settings().value('transfer/destination') == 'usb0'
    first.close()
    second = create()
    assert second.username.text() == 'Zoë Preview Player'
    assert second.ps5_destination.currentData() == 'usb0'
    second.username.setText('Second Player')
    second.close()
    assert create().username.text() == 'Second Player'
    assert set(read_settings().allKeys()) == {'player/offlineName', 'layout/instanceSplitter', 'transfer/destination'}


@pytest.mark.parametrize('saved', ['', '   ', 17, ['bad'], '\x00\n', '  A\tB  '])
def test_invalid_saved_values_are_cleaned_on_startup(preferences, saved):
    create, read_settings = preferences
    settings = read_settings()
    settings.setValue('player/offlineName', saved)
    settings.sync()
    window = create()
    expected = offline_player_name(saved)
    assert window.username.text() == expected
    assert read_settings().value('player/offlineName') == expected
    window.close()
    assert create().username.text() == expected


def test_edited_name_and_build_request_use_the_same_sanitised_value(preferences, tmp_path):
    create, read_settings = preferences
    window = create()
    source = tmp_path / 'source'
    source.mkdir()
    client = source / 'client.jar'
    client.write_bytes(b'original client')
    window.operation = 'scan'
    window.task_result(DiscoveryResult((Installation('Vanilla', 'Portable', '26.3', source, client),)))
    window.output.setText(str(tmp_path / 'package'))
    for entered in ('  Preview Player  ', 'A\x00B', '', 'Zoë 玩家'):
        window.username.setText(entered)
        expected = offline_player_name(entered)
        assert read_settings().value('player/offlineName') == expected
        requests, reason = window.build_requests()
        assert len(requests) == 1, reason
        assert requests[0].username == expected
        window.username.editingFinished.emit()
        assert window.username.text() == expected
    assert client.read_bytes() == b'original client'
    assert list(source.iterdir()) == [client]
    assert not (tmp_path / 'package').exists()


def test_gui_preferences_never_override_cli_name_or_default(preferences, tmp_path):
    create, read_settings = preferences
    create().username.setText('GUI Player')
    for options, expected in (([], 'Player'), (['--username', 'CLI Player'], 'CLI Player')):
        argv = ['packager', 'build', '--source', 'local', '--client-jar', str(tmp_path / 'client.jar'),
                '--version', '26.3', '--output', str(tmp_path / 'output'), *options]
        with patch('sys.argv', argv), patch('minecraft_playden.cli.build') as build, contextlib.redirect_stdout(io.StringIO()):
            assert main() == 0
        assert build.call_args.args[3] == expected
        assert read_settings().value('player/offlineName') == 'GUI Player'


def test_cli_controller_option_is_independent_of_gui_preference(preferences, tmp_path):
    create, read_settings = preferences
    create().controller_layout.setChecked(False)
    for options, expected in (([], True), (['--no-controller-layout'], False)):
        argv = ['packager', 'build', '--source', 'local', '--client-jar', str(tmp_path / 'client.jar'),
                '--version', '26.3', '--output', str(tmp_path / 'output'), *options]
        with patch('sys.argv', argv), patch('minecraft_playden.cli.build') as build, contextlib.redirect_stdout(io.StringIO()):
            assert main() == 0
        assert build.call_args.kwargs['controller_layout'] is expected
        assert read_settings().value('build/controllerLayout', type=bool) is False


def test_player_name_survives_an_actual_gui_process_restart(preferences):
    create, read_settings = preferences
    window = create()
    window.username.setText('Restart Player')
    window.controller_layout.setChecked(False)
    window.close()
    script = r"""
import sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from minecraft_playden.gui_app import MainWindow
app = QApplication([])
with patch('minecraft_playden.gui_app.QSettings', side_effect=lambda *args: QSettings(sys.argv[1], QSettings.Format.IniFormat)), patch('minecraft_playden.gui_app.QTimer.singleShot'):
    window = MainWindow(Path(sys.argv[1]).parent / 'cache', [])
    assert window.username.text() == 'Restart Player'
    assert not window.controller_layout.isChecked()
    assert window.progress_bar.text() == '0%'
    window.username.setText('Next Process Player')
    window.close()
"""
    result = subprocess.run([sys.executable, '-c', script, read_settings().fileName()],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    settings = read_settings()
    settings.sync()
    assert settings.value('player/offlineName') == 'Next Process Player'
    assert create().username.text() == 'Next Process Player'

@pytest.mark.parametrize('invalid', ['external', '', 123, None])
def test_invalid_destination_falls_back_to_internal(preferences, invalid):
    create, read_settings = preferences
    read_settings().setValue('transfer/destination', invalid)
    read_settings().sync()
    assert create().ps5_destination.currentData() == 'internal'


def test_controller_layout_preference_and_build_queue_capture(preferences, tmp_path):
    create, read_settings = preferences
    first = create()
    assert first.controller_layout.isChecked()
    assert first.controller_layout in first.operation_controls()
    first.controller_layout.setChecked(False)
    assert read_settings().value('build/controllerLayout', type=bool) is False
    first.close()
    window = create()
    assert not window.controller_layout.isChecked()
    source = tmp_path / 'source'
    source.mkdir()
    client = source / 'client.jar'
    client.write_bytes(b'original client')
    window.operation = 'scan'
    window.task_result(DiscoveryResult((Installation('Vanilla', 'Portable', '26.3', source, client),)))
    window.output.setText(str(tmp_path / 'package'))
    requests, reason = window.build_requests()
    assert len(requests) == 1, reason
    assert requests[0].controller_layout is False
    window.controller_layout.setChecked(True)
    assert requests[0].controller_layout is False  # Queue options are immutable snapshots.
    assert window.build_requests()[0][0].controller_layout is True
    window.close()
    assert create().controller_layout.isChecked()


def test_artwork_paths_persist_reset_and_snapshot_build_queue(preferences, tmp_path):
    from test_artwork import png
    create, settings = preferences
    icon, background = tmp_path / 'custom icon.png', tmp_path / 'custom background.png'
    icon.write_bytes(png())
    background.write_bytes(png((80, 40)))
    first = create()
    assert first.icon_artwork.text() == first.background_artwork.text() == ''
    with patch('minecraft_playden.gui_app.QFileDialog.getOpenFileName', return_value=(str(icon), 'PNG pictures (*.png)')):
        first.artwork_buttons[0].click()
    first.background_artwork.setText(str(background))
    assert settings().value('artwork/icon') == str(icon)
    assert settings().value('artwork/background') == str(background)
    first.close()
    window = create()
    assert window.icon_artwork.text() == str(icon)
    assert window.background_artwork.text() == str(background)
    source = tmp_path / 'source'
    source.mkdir()
    client = source / 'client.jar'
    client.write_bytes(b'client')
    window.operation = 'scan'
    window.task_result(DiscoveryResult((Installation('Vanilla', 'Portable', '26.3', source, client),)))
    window.output.setText(str(tmp_path / 'package'))
    requests, reason = window.build_requests()
    assert len(requests) == 1, reason
    assert requests[0].icon_artwork == icon and requests[0].background_artwork == background
    window.artwork_buttons[1].click()
    assert window.icon_artwork.text() == '' and settings().value('artwork/icon') == ''
    assert requests[0].icon_artwork == icon
    assert window.build_requests()[0][0].icon_artwork is None
    window.background_artwork.setText(str(tmp_path / 'missing.png'))
    assert window.build_requests()[0] == []
    window.artwork_buttons[3].click()
    assert window.build_requests()[0][0].background_artwork is None
    window.close()
    assert create().icon_artwork.text() == create().background_artwork.text() == ''


def test_cli_artwork_does_not_inherit_gui_preferences(preferences, tmp_path):
    create, _ = preferences
    window = create()
    window.icon_artwork.setText('/saved/icon.png')
    window.background_artwork.setText('/saved/background.png')
    for options in ([], ['--icon', str(tmp_path / 'icon.png'), '--background', str(tmp_path / 'background.png')]):
        argv = ['packager', 'build', '--source', 'local', '--client-jar', str(tmp_path / 'client.jar'),
                '--version', '26.3', '--output', str(tmp_path / 'out'), *options]
        with patch('sys.argv', argv), patch('minecraft_playden.cli.build') as build, contextlib.redirect_stdout(io.StringIO()):
            assert main() == 0
        assert build.call_args.kwargs['icon_artwork'] == (tmp_path / 'icon.png' if options else None)
        assert build.call_args.kwargs['background_artwork'] == (tmp_path / 'background.png' if options else None)
