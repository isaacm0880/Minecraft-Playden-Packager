"""Qt lifecycle and preference tests without any console connection."""
import threading
import time
from unittest.mock import patch

import pytest

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication

from minecraft_playden.receiver_manager import PairedReceiver, ReceiverInfo, VERSION
from minecraft_playden.desktop import ValidatedPackage
from minecraft_playden.network_transfer import NetworkRequest, NetworkResult
from minecraft_playden.progress import Progress
from minecraft_playden.usb_queue import TransferCandidate
from test_gui_preferences import preferences
from test_network_receiver import receiver, receiver_binary


def wait(window):
    deadline = time.monotonic() + 5
    while window.busy() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(.001)
    assert not window.busy(), window.status.text()


def install_credentials(window, key=bytes.fromhex('ab' * 32), port=9025):
    manager = window.receiver_manager
    manager.monitoring = False
    manager.install(PairedReceiver(window.ps5_host.text(), ReceiverInfo(VERSION, bytes(32), port, 9026), key, '004201'), monitor=False)


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
    install_credentials(window)
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
    assert reopened.receiver_manager.paired is None and reopened.receiver_manager.code.text() == ''
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


def test_gui_uses_the_same_recorded_instance_name_and_receives_metrics(ready, tmp_path, request):
    # Opt into the actual loopback receiver fixture lazily; Windows still runs
    # the other Qt lifecycle tests without requiring a POSIX payload process.
    receiver = request.getfixturevalue('receiver')
    from lan_fixtures import build_package
    from test_network_transfer import set_instance_name
    window, _, create, read = ready
    package = build_package(tmp_path / 'real-export')
    set_instance_name(package, 'Shared GUI Instance Name')
    window.completed_package = ValidatedPackage(package)
    window.update_build_button()
    with receiver('--diagnostics') as (sandbox, port, token):
        window.ps5_host.setText('127.0.0.1')
        install_credentials(window, bytes.fromhex(token), port)
        window.copy_to_ps5()
        wait(window)
        assert window.outcome == 'succeeded', window.log.toPlainText()
        assert window.network_results[0].destination.endswith('/Shared GUI Instance Name')
        assert window.network_results[0].receiver_metrics['published'] == 1
        assert 'Receiver:' in window.log.toPlainText()
        assert (sandbox / 'internal/Shared GUI Instance Name/recipe.json').is_file()


def test_name_override_and_pipeline_are_passed_to_worker_and_survive_repeat(ready):
    window, package, create, read = ready
    assert window.ps5_name.text() == package.name
    assert not window.ps5_pipeline.isChecked()
    window.ps5_name.setText('My PS5 Instance')
    window.ps5_pipeline.setChecked(True)
    calls = []
    def run(request, progress, cancellation):
        calls.append(request)
        return NetworkResult(package.resolve(), '/data/playden-import/My PS5 Instance', 1, 10)
    with patch.object(NetworkRequest, 'run', autospec=True, side_effect=run):
        for _ in range(2):
            window.copy_ps5_button.click()
            wait(window)
    assert all(r.package_name == 'My PS5 Instance' and r.pipeline_files for r in calls)
    assert window.ps5_name.isEnabled()
    assert 'publication confirmed' in window.status.text()
    assert '/data/playden-import/My PS5 Instance' in window.status.text()
    assert create().ps5_pipeline.isChecked()
    assert create().receiver_manager.paired is None
    window.ps5_name.setText('../escape')
    assert not window.copy_ps5_button.isEnabled()


def test_publication_receipt_loss_is_uncertain_and_never_complete(ready):
    from minecraft_playden.network_protocol import NetworkTransferError
    window, package, *_ = ready
    with patch.object(NetworkRequest, 'run', side_effect=NetworkTransferError(
            'Publication receipt was lost; check the PS5 import folder before retrying under a new name.')), \
         patch('minecraft_playden.gui_app.QMessageBox.warning'):
        window.copy_to_ps5()
        wait(window)
    assert window.outcome == 'uncertain'
    assert 'publication uncertain' in window.status.text()
    assert window.progress_bar.value() == 0
    assert not window.network_results
    assert window.copy_ps5_button.isEnabled()


def test_eta_and_verification_have_distinct_progress_labels(ready):
    window, *_ = ready
    with patch('minecraft_playden.gui_app.time.monotonic', side_effect=[10., 12.]):
        window.task_progress(Progress('network-send', 'Sending', 0, 1000, '', 0, 1000))
        assert 'ETA pending' in window.status.text()
        window.task_progress(Progress('network-send', 'Sending', 500, 1000, 'file.jar', 500, 1000))
    assert '250 B/s' in window.status.text()
    assert 'ETA 2s' in window.status.text()
    assert window.progress_bar.value() == 500
    window.task_progress(Progress('network-verify', 'Verifying', 800, 1000, '', 800, 1000))
    assert 'verified' in window.status.text()
    assert 'ETA' not in window.status.text()
    window.task_progress(Progress('network-commit', 'Publishing'))
    assert window.progress_bar.maximum() == 0


def test_build_completion_reuses_package_and_reveals_lan_settings(ready):
    window, package, *_ = ready
    window.show_completion(package, False)
    dialog = window.completion_dialog
    dialog.ps5_button.click()
    assert not dialog.isVisible()
    assert window.completed_package.path == package
    assert 'Start Receiver' in window.status.text()
    assert window.copy_ps5_button.isEnabled()


def test_validated_existing_package_becomes_the_transfer_source(ready):
    window, package, *_ = ready
    existing = package.parent / 'Existing package'
    existing.mkdir()
    with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(existing)), \
         patch('minecraft_playden.gui_app.check_package', return_value=ValidatedPackage(existing)):
        window.validate_existing()
        wait(window)
    assert window.completed_package.path == existing
    assert window.ps5_name.text() == existing.name
    assert window.usb_candidates()[0].source == existing
    assert window.copy_ps5_button.isEnabled()


def test_cancelled_queue_reports_earlier_confirmed_packages(ready):
    window, package, *_ = ready
    second = package.parent / 'Second'
    second.mkdir()
    candidates = [TransferCandidate('First', package), TransferCandidate('Second', second)]
    def run(request, progress, cancellation):
        cancellation.cancel()
        return NetworkResult(package.resolve(), '/data/playden-import/First', 1, 10)
    with patch.object(window, 'usb_candidates', return_value=candidates), \
         patch.object(NetworkRequest, 'run', autospec=True, side_effect=run) as send:
        window.copy_to_ps5()
        wait(window)
    assert send.call_count == 1
    assert window.outcome == 'cancelled'
    assert '1/2 packages confirmed on PS5' in window.log.toPlainText()
    assert package.is_dir() and second.is_dir()



def test_lan_errors_preserve_recovery_advice_without_local_rebuild_suggestion():
    from minecraft_playden.gui_components import error_message
    from minecraft_playden.network_protocol import NetworkTransferError
    detail = 'Publication receipt was lost; check /data/playden-import/Instance before retrying.'
    assert error_message(NetworkTransferError(detail)) == detail
