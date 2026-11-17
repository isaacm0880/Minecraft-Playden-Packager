"""Automatic GUI preparation using the real exporter/worker with mocked game payloads."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QMessageBox

from minecraft_playden import fabric_remap
from minecraft_playden.desktop import check_package
from minecraft_playden.discovery import DiscoveryResult, discover
from minecraft_playden.validator import validate
from test_gui_preferences import preferences
from test_fabric_remap import fixture_package, fake_remap


def pump(predicate, timeout=15):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        QApplication.instance().processEvents()
        time.sleep(.001)
    assert predicate(), 'GUI worker did not finish'


def setup_window(preferences, tmp_path, version='1.20.1'):
    package, instance = fixture_package(tmp_path, version)
    create, _ = preferences
    window = create()
    installation = next(item for item in discover([instance], include_defaults=False).installations if item.instance == instance)
    window.operation = 'scan'
    window.task_result(DiscoveryResult((installation,)))
    window.cache.setText(str(tmp_path / 'cache'))
    window.output.setText(str(tmp_path / 'GUI Package'))
    return window, package, installation


def test_fabric_defaults_to_prepared_and_build_runs_in_background(preferences, tmp_path):
    window, _, installation = setup_window(preferences, tmp_path)
    assert installation.needs_fabric_preparation is True
    assert window.prepare_fabric.isEnabled() and window.prepare_fabric.isChecked()
    entered, release = [], []
    def worker(java, args, work, token, progress=None):
        entered.append(True)
        while not release:
            token.check()
            time.sleep(.001)
        fake_remap(java, args, work, token)
    with patch.object(fabric_remap, '_run', side_effect=worker), patch('urllib.request.urlopen', side_effect=AssertionError('Warm GUI rebuild is offline')):
        window.build_button.click()
        pump(lambda: bool(entered))
        assert window.busy() and not window.copy_usb_button.isEnabled()
        assert not window.prepare_fabric.isEnabled()
        # The UI event loop is still processing while Java preparation is pending.
        QApplication.instance().processEvents()
        assert window.cancel_button.isEnabled()
        release.append(True)
        pump(lambda: not window.busy())
    assert window.outcome == 'succeeded'
    result = window.completed_package
    assert result.preparation.prepared and validate(result.path) == []
    assert (result.path / fabric_remap.RECORD).is_file()
    assert window.copy_usb_button.isEnabled() and not window.prepare_existing_button.isEnabled()
    assert 'Validating prepared Fabric cache' in window.log.toPlainText()


def test_explicit_opt_out_survives_form_edits_and_warns_before_legacy_transfer(preferences, tmp_path):
    window, _, _ = setup_window(preferences, tmp_path)
    window.prepare_fabric.setChecked(False)
    window.username.setText('Troubleshooting')
    assert not window.build_request()[0].prepare_fabric
    with patch.object(fabric_remap, '_run', side_effect=AssertionError('Explicit opt-out')), patch('urllib.request.urlopen', side_effect=AssertionError('Offline')):
        window.build_button.click()
        pump(lambda: not window.busy())
    assert window.outcome == 'succeeded' and not window.completed_package.preparation.prepared
    assert window.completed_package.preparation.required and window.prepare_existing_button.isEnabled()
    assert 'first-launch remapping' in window.fabric_package_status.text()
    with patch('minecraft_playden.gui_app.QMessageBox.warning', return_value=QMessageBox.StandardButton.No) as warning, patch('minecraft_playden.gui_app.DriveDialog') as drive:
        window.copy_usb_button.click()
    assert warning.call_args.args[1] == 'Transfer unprepared Fabric?'
    drive.assert_not_called()
    with patch('minecraft_playden.gui_app.QMessageBox.warning', return_value=QMessageBox.StandardButton.Yes):
        assert window.confirm_unprepared_transfer([window.completed_package.path])


def test_26_3_skips_preparation_without_native_java(preferences, tmp_path):
    window, _, installation = setup_window(preferences, tmp_path, '26.3')
    assert installation.needs_fabric_preparation is False
    assert not window.prepare_fabric.isChecked() and not window.prepare_fabric.isEnabled()
    with patch.object(fabric_remap, '_run', side_effect=AssertionError('Unnecessary preparation')), patch('minecraft_playden.host_java.find', side_effect=AssertionError('No native JDK required')):
        window.build_button.click()
        pump(lambda: not window.busy())
    assert window.outcome == 'succeeded' and not window.completed_package.preparation.required


def test_vanilla_bypasses_preparation_even_with_automatic_request(preferences, tmp_path):
    window, package, installation = setup_window(preferences, tmp_path)
    # Remove the loader from the actual launcher profile by selecting its base.
    base = installation.instance.parent / '1.20.1'
    vanilla = next(item for item in discover([base], include_defaults=False).installations if not item.loaders)
    window.operation = 'scan'
    window.task_result(DiscoveryResult((vanilla,)))
    window.output.setText(str(tmp_path / 'Vanilla GUI'))
    assert not window.prepare_fabric.isChecked() and not window.prepare_fabric.isEnabled()
    with patch.object(fabric_remap, 'prepare_staged', side_effect=AssertionError('Vanilla must not prepare')):
        window.build_button.click()
        pump(lambda: not window.busy())
    assert window.outcome == 'succeeded' and not window.completed_package.preparation.required


def test_missing_java_fails_without_publication_and_manual_selection_remains_available(preferences, tmp_path):
    window, _, _ = setup_window(preferences, tmp_path)
    with patch('minecraft_playden.host_java.find', side_effect=ValueError('No suitable host-native JDK. Select the Host preparation JDK.')), patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
        window.build_button.click()
        pump(lambda: not window.busy())
    assert window.outcome == 'failed' and window.completed_package is None
    assert not (tmp_path / 'GUI Package').exists() and not list(tmp_path.glob('.playden-*'))
    assert 'Host preparation JDK' in window.log.toPlainText()
    assert window.remap_java_button.isEnabled() and not window.copy_usb_button.isEnabled()
    with patch('minecraft_playden.gui_app.QFileDialog.getOpenFileName', return_value=('/chosen/jdk/bin/java', '')):
        window.remap_java_button.click()
    assert window.build_request()[0].remap_java == Path('/chosen/jdk/bin/java')


def test_cancellation_during_preparation_cleans_staging_and_never_succeeds(preferences, tmp_path):
    window, _, _ = setup_window(preferences, tmp_path)
    entered = []
    def worker(java, args, work, token, progress=None):
        entered.append(True)
        while True:
            token.check()
            time.sleep(.001)
    with patch.object(fabric_remap, '_run', side_effect=worker):
        window.build_button.click()
        pump(lambda: bool(entered))
        window.cancel_button.click()
        pump(lambda: not window.busy())
    assert window.outcome == 'cancelled' and window.completed_package is None
    assert not (tmp_path / 'GUI Package').exists() and not list(tmp_path.glob('.playden-*'))


def test_existing_package_action_produces_separate_transferable_prepared_output(preferences, tmp_path):
    window, source, _ = setup_window(preferences, tmp_path)
    original = (source / 'package.json').read_bytes()
    with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(source)):
        window.validate_button.click()
        pump(lambda: not window.busy())
    assert window.prepare_existing_button.isEnabled()
    with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(tmp_path)), patch.object(fabric_remap, '_run', side_effect=fake_remap):
        window.prepare_existing_button.click()
        pump(lambda: not window.busy())
    result = window.completed_package
    assert window.outcome == 'succeeded' and result.path != source and result.preparation.prepared
    assert validate(result.path) == [] and window.copy_usb_button.isEnabled()
    assert (source / 'package.json').read_bytes() == original
    assert not (source / fabric_remap.RECORD).exists()
    assert not window.prepare_existing_button.isEnabled()
    assert 'no additional preparation' in window.fabric_package_status.text()


def test_validated_cache_is_reused_without_jdk_discovery(preferences, tmp_path):
    source, _ = fixture_package(tmp_path, prepared=True)
    create, _ = preferences
    window = create()
    with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(source)), patch('minecraft_playden.host_java.find', side_effect=AssertionError('No repeated preparation')):
        window.validate_button.click()
        pump(lambda: not window.busy())
    assert window.completed_package.preparation.prepared and not window.prepare_existing_button.isEnabled()
    assert 'no additional preparation' in window.fabric_package_status.text()


def test_prepared_cache_survives_verified_usb_copy(preferences, tmp_path):
    from minecraft_playden.transfer import TransferRequest
    from minecraft_playden.volumes import DriveTarget, VolumeIdentity
    source, _ = fixture_package(tmp_path, prepared=True)
    usb = tmp_path / 'usb'
    usb.mkdir()
    info = usb.stat()
    identity = VolumeIdentity('fixture', 'exfat', info.st_dev, info.st_ino)
    with patch('minecraft_playden.volumes._mounted_identity', return_value=identity), patch('minecraft_playden.transfer.flush_volume'):
        result = TransferRequest(source, DriveTarget.capture(usb)).run()
    assert validate(result.destination) == []
    state = check_package(result.destination).preparation
    assert state.prepared
    record = json.loads((source / fabric_remap.RECORD).read_text())
    for name in [fabric_remap.RECORD, *record['outputs']]:
        assert (source / name).read_bytes() == (result.destination / name).read_bytes()


def test_worker_uses_actual_metadata_even_when_scan_hint_is_outdated(preferences, tmp_path):
    from dataclasses import replace
    window, _, installation = setup_window(preferences, tmp_path)
    window.operation = 'scan'
    window.task_result(DiscoveryResult((replace(installation, needs_fabric_preparation=False),)))
    window.output.setText(str(tmp_path / 'Fresh Metadata'))
    assert not window.prepare_fabric.isEnabled()  # Hint is presentation only.
    assert window.build_request()[0].prepare_fabric
    with patch.object(fabric_remap, '_run', side_effect=fake_remap) as remapper:
        window.build_button.click()
        pump(lambda: not window.busy())
    assert remapper.call_count == 1 and window.completed_package.preparation.prepared


def test_cancelling_existing_package_preparation_retains_original(preferences, tmp_path):
    window, source, _ = setup_window(preferences, tmp_path)
    window.operation = 'validate'
    window.task_result(check_package(source))
    window.update_build_button()
    before = (source / 'package.json').read_bytes()
    entered = []
    def worker(java, args, work, token, progress=None):
        entered.append(True)
        while True:
            token.check()
            time.sleep(.001)
    with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(tmp_path)), patch.object(fabric_remap, '_run', side_effect=worker):
        window.prepare_existing_button.click()
        pump(lambda: bool(entered))
        window.cancel_button.click()
        pump(lambda: not window.busy())
    assert window.outcome == 'cancelled'
    assert not (tmp_path / (source.name + ' Prepared')).exists()
    assert not list(tmp_path.glob('.playden-remap-*'))
    assert (source / 'package.json').read_bytes() == before and window.completed_package.path == source


def test_lan_warning_can_decline_an_external_unprepared_package(preferences, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import PropertyMock
    window, source, _ = setup_window(preferences, tmp_path)
    window.operation = 'validate'
    window.task_result(check_package(source))
    manager = window.receiver_manager
    manager.host.setText('127.0.0.1')
    manager.paired = SimpleNamespace(key=bytes(range(32)), info=SimpleNamespace(port=9025))
    with patch.object(type(manager), 'ready', new_callable=PropertyMock, return_value=True), patch('minecraft_playden.gui_app.QMessageBox.warning', return_value=QMessageBox.StandardButton.No) as warning, patch.object(window, 'start_task') as start:
        window.copy_to_ps5()
    assert warning.call_args.args[1] == 'Transfer unprepared Fabric?'
    start.assert_not_called()
    manager.paired = None
