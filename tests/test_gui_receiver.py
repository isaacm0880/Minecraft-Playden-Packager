"""Receiver Manager UI lifecycle, real host ELF start/pair/stop, and secret handling."""
import os
from pathlib import Path
import socket
import threading
import time
from unittest.mock import patch

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from minecraft_playden.desktop import ValidatedPackage
from minecraft_playden.network_transfer import NetworkRequest, NetworkResult
from minecraft_playden.receiver_manager import Deployment, PairedReceiver, ReceiverInfo, VERSION, ReceiverError, UnsupportedReceiver
from test_gui_preferences import preferences
from test_receiver_manager import managed, receiver_binary, wait_receiver_idle


def pump_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        QApplication.instance().processEvents()
        time.sleep(.001)
    assert predicate()


@pytest.fixture
def ui(preferences, tmp_path):
    create, settings = preferences
    window = create()
    manager = window.receiver_manager
    manager.monitoring = False
    manager.host.setText('127.0.0.1')
    package = tmp_path / 'Package'
    package.mkdir()
    window.completed_package = ValidatedPackage(package)
    window.update_build_button()
    yield window, manager, settings, create
    if window.busy():
        window.cancel_operation()
        pump_until(lambda: not window.busy())
    if manager.busy():
        manager.cancellation.cancel()
        pump_until(lambda: not manager.busy())
    manager.shutdown_local()


def test_external_receiver_requires_pairing_before_send_or_stop_then_sends_existing_package(ui, managed, tmp_path):
    from lan_fixtures import build_package
    window, manager, settings, create = ui
    package = build_package(tmp_path / 'export')
    window.completed_package = ValidatedPackage(package)
    window.update_build_button()
    assert not hasattr(window, 'ps5_token')
    with managed() as running:
        manager.control_port.setValue(running.control_port)
        manager.refresh_button.click()
        pump_until(lambda: not manager.busy())
        assert manager.state == 'Unknown'
        assert 'unverified' in manager.version.text()
        assert not manager.stop_button.isEnabled() and not window.copy_ps5_button.isEnabled()
        manager.code.setText(running.code)
        manager.pair_button.click()
        pump_until(lambda: not manager.busy())
        assert manager.state == 'Running', manager.error.text()
        assert window.copy_ps5_button.isEnabled() and manager.stop_button.isEnabled()
        key = manager.paired.key
        window.copy_ps5_button.click()
        assert not manager.stop_button.isEnabled()
        pump_until(lambda: not window.busy())
        assert window.outcome == 'succeeded', window.log.toPlainText()
        assert (running.sandbox / 'internal' / window.network_results[0].destination.split('/')[-1] / 'playden/recipe.json').exists()
        assert key.hex() not in window.log.toPlainText()
        assert not any(str(settings().value(k)) in (running.code, key.hex()) for k in settings().allKeys())
        wait_receiver_idle(manager.paired)
        manager.stop_button.click()
        assert manager.state == 'Stopping'
        pump_until(lambda: not manager.busy())
        assert manager.state == 'Stopped', manager.error.text()
        assert manager.paired is None and manager.code.text() == ''
        assert package.is_dir()
    reopened = create()
    assert reopened.receiver_manager.host.text() == '127.0.0.1'
    assert reopened.receiver_manager.control_port.value() == running.control_port
    assert reopened.receiver_manager.paired is None and reopened.receiver_manager.code.text() == ''
    assert not reopened.receiver_manager.stop_button.isEnabled()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX host fake SDK tool')
def test_gui_start_deploys_patched_elf_and_captures_encrypted_credentials_without_blocking(ui, receiver_binary, tmp_path):
    window, manager, settings, create = ui
    sdk = tmp_path / 'SDK with spaces'
    (sdk / 'bin').mkdir(parents=True)
    sandbox = tmp_path / 'console'
    sandbox.mkdir()
    deployer = sdk / 'bin/prospero-deploy'
    deployer.write_text('#!/usr/bin/env python3\nimport os, sys\np=sys.argv[-1]\nos.chmod(p,0o700)\n' +
                        f'os.execv(p,[p,"--bind","127.0.0.1","--test-root",{str(sandbox)!r}])\n')
    deployer.chmod(0o700)
    sockets = [socket.socket() for _ in range(2)]
    for stream in sockets:
        stream.bind(('127.0.0.1', 0))
    ports = [stream.getsockname()[1] for stream in sockets]
    for stream in sockets:
        stream.close()
    manager.sdk.setText(str(sdk)); manager.elf.setText(str(receiver_binary))
    manager.data_port.setValue(ports[0]); manager.control_port.setValue(ports[1])
    main_thread = threading.get_ident()
    threads, ticks = [], []
    original = Deployment.start
    def start(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)
    with patch('minecraft_playden.receiver_manager.shutil.which', return_value='/fake/socat'), patch.object(Deployment, 'start', side_effect=start, autospec=True):
        for attempt in range(2):
            manager.start_button.click()
            assert manager.state == 'Starting' and manager.busy()
            timer = QTimer(manager)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: ticks.append(True))
            timer.start(0)
            pump_until(lambda: not manager.busy())
            assert ticks and threads[-1] != main_thread
            assert manager.state == 'Running', manager.error.text()
            assert len(manager.code.text()) == 6 and manager.code.text().isascii()
            assert window.copy_ps5_button.isEnabled()
            secret = manager.paired.key
            assert secret not in (Path(manager.deployment.temporary.name) / 'receiver.elf').read_bytes()
            assert secret.hex() not in window.log.toPlainText()
            manager.stop_button.click()
            pump_until(lambda: not manager.busy())
            assert manager.state == 'Stopped', manager.error.text()
    assert settings().value('receiver/sdk') == str(sdk)
    assert settings().value('receiver/elf') == str(receiver_binary)
    assert not any(str(settings().value(k)) == secret.hex() for k in settings().allKeys())


def test_unexpected_termination_is_detected_by_monitor_and_disables_stop(ui, managed):
    window, manager, *_ = ui
    with managed() as running:
        manager.control_port.setValue(running.control_port)
        manager.monitoring = True
        manager.monitor.setInterval(25)
        manager.install(running.owner)
        assert manager.stop_button.isEnabled()
        running.process.terminate(); running.process.wait(timeout=5)
        pump_until(lambda: manager.state == 'Unknown' and not manager.busy())
        assert not manager.stop_button.isEnabled() and not window.copy_ps5_button.isEnabled()
        assert 'may have stopped' in manager.error.text()


def test_missing_receiver_and_unsupported_versions_have_actionable_errors(ui):
    window, manager, *_ = ui
    manager.sdk.setText('/missing-sdk')
    manager.elf.setText('/missing-receiver.elf')
    with patch('minecraft_playden.receiver_manager._connect', side_effect=ConnectionRefusedError):
        manager.start_button.click()
        pump_until(lambda: not manager.busy())
    assert manager.state == 'Error' and 'Receiver ELF was not found' in manager.error.text()
    assert not manager.stop_button.isEnabled()
    with patch('minecraft_playden.gui_receiver.identify', side_effect=UnsupportedReceiver('Unsupported receiver version; start the updated ELF.')):
        manager.refresh_button.click()
        pump_until(lambda: not manager.busy())
    assert manager.state == 'Error'
    assert 'Unsupported receiver version' in manager.error.text()
    assert not manager.stop_button.isEnabled()


def test_receiver_export_is_in_advanced_options_and_preserves_unpatched_elf(ui, receiver_binary, tmp_path):
    import hashlib
    import json
    window, manager, *_ = ui
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    elf = bundle / 'receiver.elf'
    elf.write_bytes(receiver_binary.read_bytes())
    (bundle / 'receiver-manifest.json').write_text(json.dumps({
        'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest()}))
    target = tmp_path / 'exported.elf'
    manager.elf.setText(str(elf))
    assert manager.export_button.parent() is manager.advanced.content
    with patch('minecraft_playden.gui_receiver.QFileDialog.getSaveFileName', return_value=(str(target), 'ELF payload (*.elf)')):
        manager.export_button.click()
    pump_until(lambda: not manager.busy())
    assert target.read_bytes() == elf.read_bytes()
    assert manager.state == 'Unknown' and str(target) in manager.error.text()


def test_stop_is_disabled_during_local_transfer_and_cannot_be_invoked_directly(ui):
    window, manager, *_ = ui
    manager.install(PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, bytes(32), 9025, 9026), bytes(32)), monitor=False)
    manager.set_transfer_active(True)
    with patch('minecraft_playden.gui_receiver.stop_receiver') as stop:
        manager.stop_button.click()
        manager.stop_receiver()
        stop.assert_not_called()
    assert not manager.stop_button.isEnabled()
    manager.set_transfer_active(False)
    assert manager.stop_button.isEnabled()


def test_cancelled_start_does_not_claim_receiver_stopped(ui):
    window, manager, *_ = ui
    entered = threading.Event()
    def start(deployment, host, sdk, elf, loader, data, control, cancellation, **kwargs):
        entered.set()
        cancellation.wait(5)
    with patch.object(Deployment, 'start', autospec=True, side_effect=start):
        manager.start_button.click()
        assert entered.wait(1)
        manager.cancel_button.click()
        pump_until(lambda: not manager.busy())
    assert manager.state == 'Unknown'
    assert 'may still be running' in manager.error.text()
    assert not manager.stop_button.isEnabled()


def test_host_change_forgets_credentials_and_code_without_network_access(ui):
    window, manager, *_ = ui
    manager.install(PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, bytes(32), 9025, 9026), b'k'*32, '000042'), monitor=False)
    assert manager.ready
    manager.host.setText('127.0.0.2')
    assert manager.paired is None and manager.code.text() == ''
    assert manager.state == 'Unknown'
    assert not window.copy_ps5_button.isEnabled() and not manager.stop_button.isEnabled()


def test_replaced_receiver_session_forgets_stale_credentials(ui, managed):
    from minecraft_playden.receiver_manager import stop_receiver
    window, manager, *_ = ui
    with managed() as first:
        manager.control_port.setValue(first.control_port)
        manager.install(first.owner, monitor=False)
        old_port, old_control = first.port, first.control_port
        stop_receiver(first.owner)
    with managed('--port', str(old_port), '--control-port', str(old_control)) as second:
        manager.refresh()
        pump_until(lambda: not manager.busy())
        assert manager.state == 'Unknown'
        assert 'session changed' in manager.error.text()
        assert manager.paired is None and manager.code.text() == ''
        assert not manager.stop_button.isEnabled() and not window.copy_ps5_button.isEnabled()


def test_sdk_process_exit_alone_does_not_mark_receiver_stopped(ui, managed):
    from unittest.mock import Mock
    window, manager, *_ = ui
    with managed() as running:
        manager.control_port.setValue(running.control_port)
        manager.install(running.owner, monitor=False)
        manager.deployment = Mock()
        manager.deployment.process.poll.return_value = 0
        manager.poll()
        pump_until(lambda: not manager.busy())
        assert manager.state == 'Running'
        assert manager.stop_button.isEnabled()


def test_external_busy_stop_refusal_preserves_running_state_and_transfer(ui, managed):
    from minecraft_playden.network_protocol import Session
    from minecraft_playden.operations import CancellationToken
    from test_network_receiver import begin, entry
    window, manager, *_ = ui
    with managed() as running:
        manager.control_port.setValue(running.control_port)
        manager.install(running.owner, monitor=False)
        with socket.create_connection(('127.0.0.1', running.port)) as stream:
            transfer = Session(stream, CancellationToken(), timeout=3)
            transfer.authenticate(running.master); begin(transfer, 1, 0)
            manager.stop_button.click()
            pump_until(lambda: not manager.busy())
            assert manager.state == 'Running' and 'busy' in manager.error.text()
            assert not manager.stop_button.isEnabled()
            entry(transfer, 'extras')
        wait_receiver_idle(manager.paired)
        manager.refresh()
        pump_until(lambda: not manager.busy())
        assert manager.state == 'Running' and manager.stop_button.isEnabled()


def test_unknown_state_never_disconnects_prior_sdk_stream_to_redeploy(ui):
    from unittest.mock import Mock
    window, manager, *_ = ui
    prior = Mock(); prior.process.poll.return_value = None
    manager.deployment = prior
    manager.set_state('Unknown', 'Connection lost')
    with patch.object(Deployment, 'start') as start:
        manager.start_button.click()
        start.assert_not_called()
    prior.close.assert_not_called()
    assert 'Refresh and authenticate' in manager.error.text()
    assert not manager.busy()


def test_stopping_external_receiver_does_not_close_another_sdk_stream(ui):
    from unittest.mock import Mock
    window, manager, *_ = ui
    owner = PairedReceiver('127.0.0.2', ReceiverInfo(VERSION, b'a'*32, 9025, 9026), b'k'*32)
    external = PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, b'b'*32, 9025, 9026), b'l'*32)
    prior = Mock(); prior.process.poll.return_value = None
    manager.deployment = prior
    manager._owned_receiver = owner
    manager.install(external, monitor=False)
    with patch('minecraft_playden.gui_receiver.stop_receiver'):
        manager.stop_button.click()
        pump_until(lambda: not manager.busy())
    assert manager.state == 'Stopped'
    prior.close.assert_not_called()
    assert manager._owned_receiver is owner


def test_returning_to_owned_endpoint_requires_refresh_but_retains_in_memory_key(ui):
    window, manager, *_ = ui
    owner = PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, bytes(32), 9025, 9026), b'k'*32)
    manager._owned_receiver = owner
    manager.install(owner, monitor=False)
    manager.host.setText('127.0.0.2')
    assert manager.paired is None
    manager.host.setText('127.0.0.1')
    assert manager.paired == owner
    assert manager.state == 'Unknown' and not manager.stop_button.isEnabled()


def test_authenticated_busy_reply_after_stop_status_is_not_reported_as_lost_contact(ui):
    from minecraft_playden.network_protocol import NetworkTransferError
    window, manager, *_ = ui
    paired = PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, bytes(32), 9025, 9026), b'k'*32)
    manager.install(paired, monitor=False)
    with patch('minecraft_playden.gui_receiver.stop_receiver', side_effect=NetworkTransferError('Receiver busy; finish or cancel the transfer before stopping.')):
        manager.stop_button.click()
        pump_until(lambda: not manager.busy())
    assert manager.state == 'Running' and manager.info.busy
    assert 'busy' in manager.error.text() and 'connection failed' not in manager.error.text()
    assert not manager.stop_button.isEnabled() and not window.copy_ps5_button.isEnabled()
