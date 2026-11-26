"""Deployment preflight and transport regressions that run on native Windows."""
import hashlib
import json
from pathlib import Path
import socket
import threading
from unittest.mock import patch

import pytest

from minecraft_playden.operations import CancellationToken
from minecraft_playden.receiver_manager import (
    BOOT_MARKER, Deployment, PairedReceiver, ReceiverError, ReceiverInfo,
    RECEIVER_PREFLIGHT_TIMEOUT, VERSION, stop_receiver, verified_bundled_receiver_elf,
)


def test_delayed_refusals_allow_preflight_to_continue():
    calls = []
    def refuse(host, port, token, *, timeout=2):
        calls.append((port, timeout))
        raise ConnectionRefusedError('delayed refusal')
    with patch('minecraft_playden.receiver_manager._connect', side_effect=refuse), \
         patch('minecraft_playden.receiver_manager.ensure_receiver_elf', side_effect=RuntimeError('passed preflight')):
        with pytest.raises(RuntimeError, match='passed preflight'):
            Deployment().start('127.0.0.1', Path('unused-sdk'), Path('unused.elf'))
    assert calls == [(9026, RECEIVER_PREFLIGHT_TIMEOUT), (9025, RECEIVER_PREFLIGHT_TIMEOUT)]
    assert RECEIVER_PREFLIGHT_TIMEOUT == 5


def test_genuine_preflight_timeout_blocks_loader():
    deployment = Deployment()
    with patch('minecraft_playden.receiver_manager._connect', side_effect=TimeoutError('timed out')) as connect, \
         patch('minecraft_playden.receiver_manager.ensure_receiver_elf') as verify, \
         patch('minecraft_playden.receiver_manager.socket.create_connection') as loader:
        with pytest.raises(ReceiverError, match=r'preflight.*127\.0\.0\.1:9026.*5 seconds.*no payload was sent'):
            deployment.start('127.0.0.1', Path('unused-sdk'), Path('unused.elf'))
    assert connect.call_count == 1
    verify.assert_not_called()
    loader.assert_not_called()
    assert not deployment.attempted


def test_ambiguous_preflight_error_reports_port_and_cause():
    with patch('minecraft_playden.receiver_manager._connect',
               side_effect=OSError(10051, 'Network unreachable')):
        with pytest.raises(ReceiverError, match=r'preflight at 127\.0\.0\.1:9026 failed:.*Network unreachable'):
            Deployment().start('127.0.0.1', Path('unused-sdk'), Path('unused.elf'))


def test_occupied_control_port_blocks_loader():
    class Occupied:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    with patch('minecraft_playden.receiver_manager._connect', return_value=Occupied()), \
         patch('minecraft_playden.receiver_manager.ensure_receiver_elf') as verify:
        with pytest.raises(ReceiverError, match=r'listening at 127\.0\.0\.1:9026'):
            Deployment().start('127.0.0.1', Path('unused-sdk'), Path('unused.elf'))
    verify.assert_not_called()


def test_loader_connection_error_names_stage_port_and_underlying_error(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    elf = bundle / 'receiver.elf'
    elf.write_bytes(b'\x7fELF\x02' + BOOT_MARKER + bytes(388))
    (bundle / 'receiver-manifest.json').write_text(json.dumps({'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest()}))
    with patch('minecraft_playden.receiver_manager._connect', side_effect=ConnectionRefusedError('unoccupied')), \
         patch('minecraft_playden.receiver_manager.socket.create_connection',
               side_effect=ConnectionRefusedError('loader refused')):
        deployment = Deployment()
        with pytest.raises(ReceiverError, match=r'ELF loader at 127\.0\.0\.1:9021 failed: loader refused'):
            deployment.start('127.0.0.1', tmp_path / 'missing-sdk', elf)
        assert not deployment.attempted


def test_payload_temp_file_error_names_stage_and_cause(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    elf = bundle / 'receiver.elf'
    elf.write_bytes(b'\x7fELF\x02' + BOOT_MARKER + bytes(388))
    (bundle / 'receiver-manifest.json').write_text(json.dumps({'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest()}))
    with patch('minecraft_playden.receiver_manager._connect', side_effect=ConnectionRefusedError('unoccupied')), \
         patch('minecraft_playden.receiver_manager.tempfile.TemporaryDirectory',
               side_effect=PermissionError('temp access denied')):
        with pytest.raises(ReceiverError, match=r'temporary folder failed: temp access denied.*No payload was sent'):
            Deployment().start('127.0.0.1', tmp_path / 'missing-sdk', elf)


def test_bundled_discovery_requires_verified_elf(tmp_path, monkeypatch):
    root = tmp_path / 'frozen'
    receiver = root / 'receiver'
    receiver.mkdir(parents=True)
    elf = receiver / 'minecraft_playden_receiver.elf'
    elf.write_bytes(b'\x7fELF\x02' + BOOT_MARKER + bytes(388))
    (receiver / 'receiver-manifest.json').write_text(json.dumps({'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest(),
                                                                 'source_commit': 'test-commit'}))
    (root / 'build-metadata.json').write_text(json.dumps({'source_commit': 'test-commit'}))
    monkeypatch.setattr('sys._MEIPASS', str(root), raising=False)
    assert verified_bundled_receiver_elf() == elf
    elf.write_bytes(elf.read_bytes() + b'changed')
    assert verified_bundled_receiver_elf() is None


def test_verified_bundle_uploads_without_sdk_utilities_and_reports_stages(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    elf = bundle / 'minecraft_playden_receiver.elf'
    elf.write_bytes(b'\x7fELF\x02' + BOOT_MARKER + bytes(388))
    (bundle / 'receiver-manifest.json').write_text(json.dumps({'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest()}))
    loader = socket.socket()
    loader.bind(('127.0.0.1', 0))
    loader.listen(1)
    loader.settimeout(5)
    loader_port = loader.getsockname()[1]
    receiver_ports = []
    for _ in range(2):
        candidate = socket.socket()
        candidate.bind(('127.0.0.1', 0))
        receiver_ports.append(candidate.getsockname()[1])
        candidate.close()
    received = []
    def serve():
        with loader, loader.accept()[0] as stream:
            chunks = []
            while block := stream.recv(65536):
                chunks.append(block)
            received.append(b''.join(chunks))
            stream.sendall(b'PDLAN2_BOOTSTRAP ' + b'0' * 768 + b'\n')
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    paired = PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, bytes(32), *receiver_ports), bytes(32))
    events = []
    deployment = Deployment()
    try:
        with patch('minecraft_playden.receiver_manager.shutil.which', side_effect=AssertionError('SDK utility requested')), \
             patch('minecraft_playden.receiver_manager.decode_bootstrap', return_value=paired), \
             patch('minecraft_playden.receiver_manager.receiver_status', return_value=paired.info):
            result = deployment.start('127.0.0.1', tmp_path / 'missing-sdk', elf, loader_port,
                                      receiver_ports[0], receiver_ports[1],
                                      progress=events.append, cancellation=CancellationToken())
        assert result == paired
        assert [event.stage for event in events] == [
            'receiver-check', 'receiver-connect', 'receiver-upload', 'receiver-startup',
            'receiver-authenticate', 'receiver-ready']
        assert received and received[0].startswith(b'\x7fELF') and received[0] != elf.read_bytes()
    finally:
        deployment.close()
        thread.join(5)
    assert not thread.is_alive()


def test_authenticated_stop_confirms_delayed_refusals_on_both_ports():
    class Control:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    class Session:
        def __init__(self):
            self.receipts = iter((b'info', b'stopping'))
        def ack(self):
            return next(self.receipts)
        def send(self, *args):
            pass
    info = ReceiverInfo(VERSION, bytes(32), 9025, 9026, stop_lease=b'x' * 16)
    paired = PairedReceiver('127.0.0.1', info, bytes(32))
    calls = []
    def connect(host, port, token, *, timeout=2):
        calls.append((port, timeout))
        if port == info.port:
            raise ConnectionRefusedError('delayed refusal')
        return Control()
    with patch('minecraft_playden.receiver_manager._connect', side_effect=connect), \
         patch('minecraft_playden.receiver_manager._authenticated', return_value=Session()), \
         patch('minecraft_playden.receiver_manager._info', return_value=info), \
         patch('minecraft_playden.receiver_manager.identify', side_effect=ConnectionRefusedError('delayed refusal')) as identify:
        stop_receiver(paired)
    assert calls == [(9026, 2), (9025, RECEIVER_PREFLIGHT_TIMEOUT)]
    assert identify.call_args.kwargs['connect_timeout'] == RECEIVER_PREFLIGHT_TIMEOUT


def test_stop_does_not_claim_success_when_closure_times_out():
    class Control:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    class Session:
        def __init__(self):
            self.receipts = iter((b'info', b'stopping'))
        def ack(self):
            return next(self.receipts)
        def send(self, *args):
            pass
    info = ReceiverInfo(VERSION, bytes(32), 9025, 9026, stop_lease=b'x' * 16)
    paired = PairedReceiver('127.0.0.1', info, bytes(32))
    with patch('minecraft_playden.receiver_manager._connect', return_value=Control()), \
         patch('minecraft_playden.receiver_manager._authenticated', return_value=Session()), \
         patch('minecraft_playden.receiver_manager._info', return_value=info), \
         patch('minecraft_playden.receiver_manager.identify', side_effect=TimeoutError('timed out')), \
         patch('minecraft_playden.receiver_manager.STOP_CONFIRM_TIMEOUT', .15):
        with pytest.raises(ReceiverError, match='listener closure.*not confirmed'):
            stop_receiver(paired)
