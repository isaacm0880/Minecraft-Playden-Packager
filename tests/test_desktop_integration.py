"""Folder opening regressions, including the environment inherited by KDE helpers."""
import os
from pathlib import Path
import shlex
import time
from unittest.mock import patch

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from minecraft_playden.desktop import ValidatedPackage
from minecraft_playden.desktop_integration import desktop_environment, needs_system_opener, open_system_folder
from test_gui_preferences import preferences


def test_frozen_environment_restores_host_libraries_without_mutating_parent():
    values = {'LD_LIBRARY_PATH': '/bundle/lib', 'LD_LIBRARY_PATH_ORIG': '/host/lib',
              'QT_PLUGIN_PATH': '/bundle/qt', 'QT_QPA_PLATFORM_PLUGIN_PATH': '/bundle/platforms',
              'QML2_IMPORT_PATH': '/bundle/qml', 'PYTHONHOME': '/bundle/python',
              'PYTHONPATH': '/bundle/modules', 'DISPLAY': ':0', 'DBUS_SESSION_BUS_ADDRESS': 'session',
              'XDG_CURRENT_DESKTOP': 'KDE', 'WAYLAND_DISPLAY': 'wayland-0'}
    with patch.dict(os.environ, values, clear=True):
        clean = desktop_environment()
        assert clean == {'LD_LIBRARY_PATH': '/host/lib', 'DISPLAY': ':0',
                         'DBUS_SESSION_BUS_ADDRESS': 'session', 'XDG_CURRENT_DESKTOP': 'KDE',
                         'WAYLAND_DISPLAY': 'wayland-0'}
        assert os.environ == values
        del os.environ['LD_LIBRARY_PATH_ORIG']
        assert 'LD_LIBRARY_PATH' not in desktop_environment()


def test_appimage_and_frozen_linux_use_system_opener():
    with patch('sys.platform', 'linux'), patch.dict(os.environ, {'APPIMAGE': '/tmp/app.AppImage'}):
        assert needs_system_opener()
    with patch('sys.platform', 'linux'), patch.dict(os.environ, {}, clear=True), patch('sys.frozen', True, create=True):
        assert needs_system_opener()
    with patch('sys.platform', 'win32'), patch('sys.frozen', True, create=True):
        assert not needs_system_opener()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX desktop helper')
@pytest.mark.parametrize('exit_code', [0, 4])
def test_async_helper_receives_path_as_one_argument_and_reports_exit(preferences, tmp_path, exit_code):
    window = preferences[0]()
    output = tmp_path / 'argument.txt'
    script = tmp_path / 'fake-xdg-open'
    script.write_text('#!/bin/sh\nprintf "%s" "$1" > ' + shlex.quote(str(output)) +
                      f'\necho "No folder handler" >&2\nexit {exit_code}\n')
    script.chmod(0o755)
    class Helper(QProcess):
        def setProgram(self, program):
            assert program == '/usr/bin/xdg-open'
            super().setProgram(str(script))
    errors, done = [], []
    with patch('minecraft_playden.desktop_integration.QProcess', Helper):
        process = open_system_folder(tmp_path / 'name with spaces;$(literal)', window, errors.append)
        process.finished.connect(lambda *args: done.append(True))
        deadline = time.monotonic() + 5
        while not done and time.monotonic() < deadline:
            QApplication.instance().processEvents()
            time.sleep(.001)
    assert done
    assert output.read_text() == str(tmp_path / 'name with spaces;$(literal)')
    assert bool(errors) == bool(exit_code)
    if errors:
        assert 'status 4' in errors[0] and 'No folder handler' in errors[0]


def test_gui_routes_appimage_errors_to_log_and_dialog(preferences, tmp_path):
    window = preferences[0]()
    window.completed_package = ValidatedPackage(tmp_path)
    window.update_build_button()
    with patch('minecraft_playden.gui_app.needs_system_opener', return_value=True), \
         patch('minecraft_playden.gui_app.open_system_folder', return_value=QProcess(window)) as opened, \
         patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
        window.open_output_button.click()
        assert opened.call_args.args[0] == tmp_path.resolve()
        opened.call_args.args[2]('xdg-open exited with status 4')
    assert 'Dolphin' in warning.call_args.args[2]
    assert str(tmp_path) in window.log.toPlainText()


def test_missing_system_opener_reports_launch_failure(preferences, tmp_path):
    window = preferences[0]()
    class MissingHelper(QProcess):
        def setProgram(self, program):
            super().setProgram(str(tmp_path / 'missing-xdg-open'))
    errors = []
    with patch('minecraft_playden.desktop_integration.QProcess', MissingHelper):
        open_system_folder(tmp_path, window, errors.append)
        deadline = time.monotonic() + 5
        while not errors and time.monotonic() < deadline:
            QApplication.instance().processEvents()
            time.sleep(.001)
    assert len(errors) == 1
    assert 'xdg-open' in errors[0]
