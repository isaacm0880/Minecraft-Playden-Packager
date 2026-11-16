"""Qt lifecycle and preference tests without any console connection."""
import threading
import time
from unittest.mock import patch

import pytest

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication

from minecraft_playden.desktop import ValidatedPackage
from minecraft_playden.network_transfer import NetworkRequest, NetworkResult
from minecraft_playden.progress import Progress
from minecraft_playden.usb_queue import TransferCandidate
from test_gui_preferences import preferences


def wait(window):
    deadline = time.monotonic() + 5
    while window.busy() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(.001)
    assert not window.busy(), window.status.text()


@pytest.fixture
def ready(preferences, tmp_path):
    create, read = preferences
    window = create()
    package = tmp_path / 'Package'
    package.mkdir()
    (package / 'sentinel').write_bytes(b'keep local')
    window.completed_package = ValidatedPackage(package)
    window.update_build_button()
    window.ps5_host.setText('192.168.1.50')
    window.ps5_token.setText('ab' * 32)
    yield window, package, create, read
    if window.busy():
        window.cancel_operation()
        wait(window)


def test_settings_persist_host_and_usb_destination_but_never_pairing_token(ready):
    window, package, create, read = ready
    assert window.ps5_destination.currentData() == 'internal'
    assert window.copy_ps5_button.isEnabled()
    window.ps5_destination.setCurrentIndex(1)
    assert read().value('transfer/destination') == 'usb0'
    window.close()
    reopened = create()
    assert reopened.ps5_destination.currentData() == 'usb0'
    assert reopened.ps5_host.text() == '192.168.1.50'
    assert reopened.ps5_token.text() == ''
    assert not reopened.copy_ps5_button.isEnabled()
    assert 'ab' * 32 not in [str(read().value(key)) for key in read().allKeys()]


def test_worker_sends_selected_destination_and_restores_controls_without_usb_cleanup(ready):
    window, package, create, read = ready
    window.ps5_destination.setCurrentIndex(1)
    window.cleanup_after_usb.setChecked(True)
    main_thread = threading.get_ident()
    calls = []
    def run(request, progress, cancellation):
        assert threading.get_ident() != main_thread
        calls.append(request)
        progress(Progress('network-send', 'Sending', 10, 10, 'sentinel', 10, 10))
        return NetworkResult(package.resolve(), '/mnt/usb0/playden/import/Package', 1, 10)
    with patch.object(NetworkRequest, 'run', autospec=True, side_effect=run), patch.object(window, 'maybe_cleanup') as cleanup:
        window.copy_ps5_button.click()
        assert window.busy()
        assert not window.ps5_destination.isEnabled()
        wait(window)
        cleanup.assert_not_called()
    assert calls[0].destination == 'usb0'
    assert calls[0].host == '192.168.1.50'
    assert window.outcome == 'succeeded'
    assert window.progress_bar.value() == 1000
    assert window.ps5_destination.isEnabled()
    assert window.copy_usb_button.isEnabled()
    assert window.copy_ps5_button.isEnabled()
    assert (package / 'sentinel').read_bytes() == b'keep local'
    assert len(window.network_results) == 1
    assert 'ab' * 32 not in window.log.toPlainText()


def test_network_worker_is_cancellable_and_does_not_delete_local_files(ready):
    window, package, create, read = ready
    entered = threading.Event()
    def run(request, progress, cancellation):
        entered.set()
        cancellation.wait(5)
    with patch.object(NetworkRequest, 'run', autospec=True, side_effect=run):
        window.copy_ps5_button.click()
        assert entered.wait(1)
        assert window.cancel_button.isEnabled()
        window.cancel_button.click()
        wait(window)
    assert window.outcome == 'cancelled'
    assert 'PS5 LAN transfer cancelled' in window.status.text()
    assert (package / 'sentinel').exists()


def test_partial_queue_stops_on_error_and_reports_previously_confirmed_packages(ready):
    window, package, create, read = ready
    second = package.parent / 'Second'
    second.mkdir()
    candidates = [TransferCandidate('First', package), TransferCandidate('Second', second)]
    result = NetworkResult(package.resolve(), '/data/playden-import/Package', 1, 10)
    with patch.object(window, 'usb_candidates', return_value=candidates), patch.object(NetworkRequest, 'run', autospec=True, side_effect=[result, OSError('Insufficient storage')]) as run, patch('minecraft_playden.gui_app.QMessageBox.warning'):
        window.copy_to_ps5()
        wait(window)
    assert run.call_count == 2
    assert window.outcome == 'failed'
    assert '1/2 packages confirmed' in window.log.toPlainText()
    assert 'Insufficient storage' in window.log.toPlainText()
    assert package.exists() and second.exists()
