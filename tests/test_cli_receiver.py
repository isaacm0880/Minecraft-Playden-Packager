"""Automatic CLI lifecycle uses the existing encrypted SDK path; loopback only."""
import io
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

import pytest

from minecraft_playden.cli import main
from minecraft_playden.operations import CancellationToken, OperationCancelled
from minecraft_playden.receiver_manager import Deployment, ReceiverConfiguration, ReceiverError, UnsupportedReceiver, ensure_receiver_elf, BOOT_MARKER
from test_receiver_manager import managed, receiver_binary


def cli(monkeypatch, tmp_path, *options):
    monkeypatch.setattr(sys, 'argv', ['packager', 'transfer-ps5', str(tmp_path / 'package'),
                                    '--host', '127.0.0.1', '--start-receiver', *options])


def config(tmp_path):
    return ReceiverConfiguration(tmp_path / 'sdk', tmp_path / 'receiver.elf')


def test_configuration_reads_gui_keys_and_environment_defaults(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('PS5_PAYLOAD_SDK', str(tmp_path / 'environment-sdk'))
    settings = SimpleNamespace(value=lambda key, default: {
        'receiver/sdk': '', 'receiver/elf': str(tmp_path / 'configured.elf'),
        'receiver/loaderPort': 9101, 'receiver/dataPort': 9105, 'receiver/controlPort': 9106,
    }.get(key, default))
    result = ReceiverConfiguration.load(settings)
    assert result.sdk == tmp_path / 'environment-sdk'
    assert result.elf == tmp_path / 'configured.elf'
    assert (result.loader_port, result.port, result.control_port) == (9101, 9105, 9106)


@pytest.mark.parametrize('failure,exit_code', [(ReceiverError('SDK deployment failed; loader output unavailable.'), 1),
                                             (KeyboardInterrupt(), 130), (OperationCancelled('cancelled'), 130)])
def test_failed_start_or_cancel_closes_connection_and_never_transfers(tmp_path, monkeypatch, capsys, failure, exit_code):
    cli(monkeypatch, tmp_path)
    with patch.object(ReceiverConfiguration, 'load', return_value=config(tmp_path)), \
         patch.object(Deployment, 'start', side_effect=failure), patch.object(Deployment, 'close') as close, \
         patch('minecraft_playden.network_transfer.NetworkRequest.run') as transfer, patch('getpass.getpass') as prompt:
        assert main() == exit_code
    close.assert_called_once()
    transfer.assert_not_called()
    prompt.assert_not_called()
    assert 'received and verified' not in capsys.readouterr().out


def test_cli_options_override_configuration_and_use_authenticated_data_port(tmp_path, monkeypatch):
    from minecraft_playden.receiver_manager import PairedReceiver, ReceiverInfo, VERSION
    from minecraft_playden.network_transfer import NetworkResult
    cli(monkeypatch, tmp_path, '--sdk', str(tmp_path / 'override-sdk'), '--receiver-elf', str(tmp_path / 'override.elf'),
        '--loader-port', '9301', '--port', '9305', '--control-port', '9306')
    paired = PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, bytes(32), 9405, 9306), b'k' * 32)
    with patch.object(ReceiverConfiguration, 'load', return_value=config(tmp_path)), \
         patch.object(Deployment, 'start', return_value=paired) as start, patch.object(Deployment, 'close'), \
         patch('minecraft_playden.network_transfer.NetworkRequest.run', return_value=NetworkResult(tmp_path, '/data/playden-import/package', 1, 1)) as run:
        assert main() == 0
    assert start.call_args.args[1:6] == (tmp_path / 'override-sdk', tmp_path / 'override.elf', 9301, 9305, 9306)
    assert run.call_args.kwargs['cancellation'].cancelled


@pytest.mark.skipif(os.name != 'posix', reason='POSIX fake SDK script')
@pytest.mark.parametrize('interactive', [False, True])
def test_cli_deploys_real_encrypted_receiver_and_transfers_without_a_code_prompt(tmp_path, monkeypatch, receiver_binary, interactive):
    from lan_fixtures import build_package
    from minecraft_playden.receiver_manager import receiver_status
    package = build_package(tmp_path / 'export')
    sdk = tmp_path / 'SDK with spaces'
    (sdk / 'bin').mkdir(parents=True)
    sandbox = tmp_path / 'console'; sandbox.mkdir()
    deployer = sdk / 'bin/prospero-deploy'
    deployer.write_text('#!/usr/bin/env python3\nimport os,sys\np=sys.argv[-1]\nos.chmod(p,0o700)\n' +
                        f'os.execv(p,[p,"--bind","127.0.0.1","--test-root",{str(sandbox)!r}])\n')
    deployer.chmod(0o700)
    streams = [socket.socket() for _ in range(3)]
    for stream in streams:
        stream.bind(('127.0.0.1', 0))
    loader, data, control = [stream.getsockname()[1] for stream in streams]
    for stream in streams:
        stream.close()
    cfg = ReceiverConfiguration(sdk, receiver_binary, loader, data, control)
    monkeypatch.delenv('PLAYDEN_PS5_TOKEN', raising=False)
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setattr(sys, 'argv', ['packager', 'transfer-ps5', str(package), '--host', '127.0.0.1', '--start-receiver', '--name', 'CLI Progress Test'])
    output, error = io.StringIO(), io.StringIO()
    monkeypatch.setattr(output, 'isatty', lambda: interactive)
    monkeypatch.setattr(error, 'isatty', lambda: interactive)
    monkeypatch.setattr(sys, 'stdout', output); monkeypatch.setattr(sys, 'stderr', error)
    original, sessions = Deployment.start, []
    def start(*args, **kwargs):
        paired = original(*args, **kwargs)
        assert receiver_status(paired).port == data
        sessions.append(paired)
        return paired
    with patch.object(ReceiverConfiguration, 'load', return_value=cfg), \
         patch('minecraft_playden.receiver_manager.shutil.which', return_value='/fake/socat'), \
         patch.object(Deployment, 'start', autospec=True, side_effect=start), \
         patch('minecraft_playden.receiver_manager.stop_receiver') as stop, patch('getpass.getpass') as prompt:
        assert main() == 0, error.getvalue()
    prompt.assert_not_called(); stop.assert_not_called()
    assert (sandbox / 'internal/CLI Progress Test/playden/recipe.json').is_file()
    text = output.getvalue() + error.getvalue()
    assert sessions[0].key.hex() not in text and sessions[0].pairing_code not in text
    assert 'PDLAN2_BOOTSTRAP' not in text
    assert 'encrypted startup' in text.lower() and 'authenticat' in text.lower()
    assert 'received and verified' in text and 'ETA' in text and 'Verifying' in text
    assert ('\r' in error.getvalue()) == interactive


def test_running_receiver_requires_authenticated_access_and_is_never_replaced(managed, tmp_path):
    with managed() as running, patch('minecraft_playden.receiver_manager.subprocess.Popen') as launch:
        deploy = Deployment()
        with pytest.raises(ReceiverError, match='already listening'):
            deploy.start('127.0.0.1', tmp_path / 'missing-sdk', tmp_path / 'missing.elf',
                         port=running.port, control_port=running.control_port)
        paired = deploy.start('127.0.0.1', tmp_path / 'missing-sdk', tmp_path / 'missing.elf',
                              port=running.port, control_port=running.control_port, existing_key=running.master)
        assert paired.key == running.master
        with pytest.raises(ReceiverError, match='could not be authenticated'):
            deploy.start('127.0.0.1', tmp_path, tmp_path / 'missing.elf', port=running.port,
                         control_port=running.control_port, existing_key=b'x' * 32)
        with patch('minecraft_playden.receiver_manager.identify', side_effect=UnsupportedReceiver('unsupported version')):
            with pytest.raises(ReceiverError, match='incompatible'):
                deploy.start('127.0.0.1', tmp_path, tmp_path / 'missing.elf', port=running.port,
                             control_port=running.control_port, existing_key=running.master)
        launch.assert_not_called()
        assert running.process.poll() is None


@pytest.mark.parametrize('failure', [TimeoutError('timeout'), OSError('connection lost')])
def test_uncertain_receiver_ports_never_deploy(tmp_path, failure):
    with patch('minecraft_playden.receiver_manager._connect', side_effect=failure), \
         patch('minecraft_playden.receiver_manager.subprocess.Popen') as launch:
        with pytest.raises(ReceiverError, match='deployment was not attempted'):
            Deployment().start('127.0.0.1', tmp_path, tmp_path / 'receiver.elf')
    launch.assert_not_called()


def test_missing_sdk_is_actionable_before_launch(tmp_path):
    with patch('minecraft_playden.receiver_manager._connect', side_effect=ConnectionRefusedError()), \
         patch('minecraft_playden.receiver_manager.subprocess.Popen') as launch:
        with pytest.raises(ReceiverError, match='SDK deployer was not found'):
            Deployment().start('127.0.0.1', tmp_path, tmp_path / 'missing.elf')
    launch.assert_not_called()


def test_missing_build_toolchain_never_starts_a_process(tmp_path):
    (tmp_path / 'main.c').write_text('/* fixture */')
    (tmp_path / 'Makefile').write_text('all:\n')
    with patch('minecraft_playden.receiver_manager.subprocess.Popen') as launch:
        with pytest.raises(ReceiverError, match='toolchain/prospero.mk'):
            ensure_receiver_elf(tmp_path / 'sdk', tmp_path / 'receiver.elf', CancellationToken())
    launch.assert_not_called()


@pytest.mark.skipif(os.name != 'posix', reason='Fake local build tool')
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel', 'invalid'])
def test_receiver_build_is_atomic_cancellable_and_cleans_temporary_files(tmp_path, outcome):
    source, sdk = tmp_path / 'receiver', tmp_path / 'sdk'
    source.mkdir(); (sdk / 'toolchain').mkdir(parents=True)
    (sdk / 'toolchain/prospero.mk').write_text('')
    (source / 'main.c').write_text('/* fixture */')
    (source / 'Makefile').write_text('all:\n')
    elf = source / 'receiver.elf'
    tool = tmp_path / 'make'
    data = b'\x7fELF\x02' + BOOT_MARKER + bytes(388)
    body = 'import sys,time\n'
    if outcome == 'failure': body += 'sys.exit(1)\n'
    elif outcome == 'cancel': body += 'time.sleep(30)\n'
    else: body += f'open(sys.argv[-1].split("=",1)[1],"wb").write({data if outcome == "success" else b"invalid"!r})\n'
    tool.write_text('#!' + sys.executable + '\n' + body); tool.chmod(0o700)
    token = CancellationToken()
    if outcome == 'cancel':
        import threading
        timer = threading.Timer(.2, token.cancel); timer.start()
    try:
        with patch('minecraft_playden.receiver_manager.shutil.which', return_value=str(tool)):
            if outcome == 'success':
                ensure_receiver_elf(sdk, elf, token)
                assert elf.read_bytes() == data
            else:
                with pytest.raises(OperationCancelled if outcome == 'cancel' else ReceiverError):
                    ensure_receiver_elf(sdk, elf, token)
                assert not elf.exists()
    finally:
        if outcome == 'cancel': timer.join()
    assert not list(source.glob('playden-receiver-build-*'))


def test_cli_reuses_authenticated_receiver_without_sdk_or_stopping_it(managed, tmp_path, monkeypatch, capsys):
    from lan_fixtures import build_package
    package = build_package(tmp_path / 'export')
    with managed() as running:
        monkeypatch.setenv('PLAYDEN_PS5_TOKEN', running.master.hex())
        monkeypatch.setattr(sys, 'argv', ['packager', 'transfer-ps5', str(package), '--host', '127.0.0.1', '--start-receiver'])
        cfg = ReceiverConfiguration(tmp_path / 'missing-sdk', tmp_path / 'missing.elf', port=running.port, control_port=running.control_port)
        with patch.object(ReceiverConfiguration, 'load', return_value=cfg), \
             patch('minecraft_playden.receiver_manager.subprocess.Popen') as launch, patch('getpass.getpass') as prompt:
            assert main() == 0
        launch.assert_not_called(); prompt.assert_not_called()
        assert running.process.poll() is None
        assert (running.sandbox / 'internal' / package.name / 'playden/recipe.json').is_file()
        logs = capsys.readouterr()
        assert 'Existing receiver authenticated' in logs.err
        assert running.master.hex() not in logs.out + logs.err and running.code not in logs.out + logs.err


def test_start_refuses_busy_authenticated_receiver_without_interrupting_it(managed, tmp_path):
    from minecraft_playden.network_protocol import Session
    from test_network_receiver import begin, entry
    with managed() as running, socket.create_connection(('127.0.0.1', running.port)) as data:
        session = Session(data, CancellationToken())
        session.authenticate(running.master)
        begin(session, 1, 0)
        with pytest.raises(ReceiverError, match='busy'), patch('minecraft_playden.receiver_manager.subprocess.Popen') as launch:
            Deployment().start('127.0.0.1', tmp_path, tmp_path / 'missing.elf', port=running.port,
                               control_port=running.control_port, existing_key=running.master)
        launch.assert_not_called()
        assert running.process.poll() is None
        entry(session, 'extras')


@pytest.mark.parametrize('options', [('--pair',), ('--sdk', '/local/sdk')])
def test_start_flags_are_explicit_and_mutually_exclusive(tmp_path, monkeypatch, options):
    cli(monkeypatch, tmp_path, *options)
    if options[0] == '--sdk':
        sys.argv.remove('--start-receiver')
    with patch.object(Deployment, 'start') as start, pytest.raises(SystemExit) as exit:
        main()
    assert exit.value.code == 2
    start.assert_not_called()
