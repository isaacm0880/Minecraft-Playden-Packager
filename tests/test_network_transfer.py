"""Real exporter -> Python LAN client -> actual C receiver, entirely on loopback."""
import io
import json
import socket
from unittest.mock import patch

import pytest

from minecraft_playden.cli import main
from minecraft_playden.desktop import LocalBuildRequest
from minecraft_playden.discovery import discover
from minecraft_playden.network_transfer import NetworkRequest, NetworkResult
from minecraft_playden.network_protocol import COMMIT, NetworkTransferError, Session
from minecraft_playden.operations import CancellationToken, OperationCancelled
from minecraft_playden.validator import validate
from test_fabric_launch import installed, archive, save
from test_network_receiver import receiver, receiver_binary, wait_clean


@pytest.fixture
def built_package(tmp_path):
    instance, metadata, _ = installed(tmp_path / 'source', '26.3', '0.20.1')
    payloads = {
        'https://fixture/netty': archive({'io/netty/channel/unix/Errors.class': b'class'}),
        'https://fixture/fastutil': archive({'it/unimi/dsi/fastutil/floats/FloatList.class': b'class'}),
        'https://fixture/native': archive({'lwjgl.dll': b'MZnative'}),
        'https://fixture/index': b'{"objects":{}}',
        'https://fixture/base': json.dumps(metadata).encode(),
    }
    def request(req, **kwargs):
        url = req.full_url
        if url.endswith('version_manifest_v2.json'):
            return io.BytesIO(json.dumps({'versions': [{'id': metadata['id'], 'url': 'https://fixture/base'}]}).encode())
        return io.BytesIO(payloads[url])
    def runtime(metadata, downloader, java):
        for path in ['bin/java.exe', 'bin/server/jvm.dll', 'release']:
            save(java, path, b'Windows-x64-fixture')
        return {'fixture': 'windows x64'}
    source = discover([instance], include_defaults=False).installations[0]
    output = tmp_path / 'Minecraft Package'
    with patch('urllib.request.urlopen', side_effect=request), patch('minecraft_playden.runtime.install', side_effect=runtime):
        LocalBuildRequest(source, output, tmp_path / 'cache').run()
    assert validate(output) == []
    return output


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('destination,root', [('internal', 'internal'), ('usb0', 'usb0/playden/import')])
def test_complete_client_transfer_preserves_every_local_byte(receiver, built_package, destination, root):
    before = snapshot(built_package)
    events = []
    with receiver() as (sandbox, port, token):
        result = NetworkRequest(built_package, '127.0.0.1', token, destination, port).run(events.append)
        remote = sandbox / root / built_package.name
        assert result.source == built_package.resolve()
        assert result.files == len(before)
        assert result.bytes_copied == sum(map(len, before.values()))
        assert snapshot(remote) == before == snapshot(built_package)
        assert validate(remote) == []
        assert events[-1].stage == 'network-complete'
        assert {'network-send', 'network-verify', 'network-commit'}.issubset({event.stage for event in events})
        assert token not in repr(NetworkRequest(built_package, '127.0.0.1', token))


@pytest.mark.parametrize('when', ['network-send', 'network-verify'])
def test_cancellation_keeps_local_output_and_no_remote_publication(receiver, built_package, when):
    before = snapshot(built_package)
    cancellation = CancellationToken()
    def progress(event):
        if event.stage == when:
            cancellation.cancel()
    with receiver() as (sandbox, port, token):
        with pytest.raises(OperationCancelled):
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run(progress, cancellation)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()
        assert snapshot(built_package) == before


def test_cancellation_before_commit_prevents_publication(receiver, built_package):
    cancellation = CancellationToken()
    def progress(event):
        if event.stage == 'network-commit':
            cancellation.cancel()
    # A cancellation already set before sending COMMIT must still prevent it.
    with receiver() as (sandbox, port, token):
        with pytest.raises(OperationCancelled):
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run(progress, cancellation)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()


def test_cancellation_after_commit_resolves_receipt(receiver, built_package):
    cancellation = CancellationToken()
    original = Session.send
    def send(session, kind, body=b''):
        original(session, kind, body)
        if kind == COMMIT:
            cancellation.cancel()
    with receiver() as (sandbox, port, token), patch.object(Session, 'send', send):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port).run(cancellation=cancellation)
        assert result.destination.endswith('/' + built_package.name)
        assert (sandbox / 'internal' / built_package.name / 'recipe.json').is_file()


def test_lost_completion_receipt_reports_uncertainty_and_keeps_files(receiver, built_package):
    original_send, original_ack = Session.send, Session.ack
    committed = False
    def send(session, kind, data=b''):
        nonlocal committed
        original_send(session, kind, data)
        if kind == COMMIT:
            committed = True
    def ack(session):
        if committed:
            raise NetworkTransferError('synthetic lost receipt')
        return original_ack(session)
    with receiver() as (sandbox, port, token), patch.object(Session, 'send', send), patch.object(Session, 'ack', ack):
        with pytest.raises(NetworkTransferError, match='receipt was lost'):
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run()
        assert built_package.exists()


def test_invalid_local_packages_are_rejected_before_connecting(built_package):
    (built_package / 'playden.args').write_bytes(b'corrupt')
    with patch('socket.create_connection') as connect, pytest.raises(ValueError, match='validation failed'):
        NetworkRequest(built_package, '127.0.0.1', '00' * 32).run()
    connect.assert_not_called()


def test_local_symlinks_are_rejected_before_reading_or_connecting(built_package, tmp_path):
    target = tmp_path / 'outside'
    target.write_bytes(b'private')
    try:
        (built_package / 'link').symlink_to(target)
    except OSError:
        pytest.skip('Host cannot create fixture symlink')
    with patch('socket.create_connection') as connect, pytest.raises(ValueError, match='links'):
        NetworkRequest(built_package, '127.0.0.1', '00' * 32).run()
    connect.assert_not_called()
    assert target.read_bytes() == b'private'


def test_source_mutation_during_upload_prevents_commit(receiver, built_package):
    changed = False
    def progress(event):
        nonlocal changed
        if event.stage == 'network-send' and not changed:
            changed = True
            (built_package / 'new-file').write_bytes(b'changed')
    with receiver() as (sandbox, port, token):
        with pytest.raises(ValueError, match='changed during upload'):
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run(progress)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()


def test_cli_destination_override_does_not_use_gui_settings(tmp_path, monkeypatch, capsys):
    token = 'ab' * 32
    monkeypatch.setenv('PLAYDEN_PS5_TOKEN', token)
    monkeypatch.setattr('sys.argv', ['packager', 'transfer-ps5', str(tmp_path), '--host', '192.168.1.50', '--destination', 'usb0', '--name', 'Fresh Package'])
    result = NetworkResult(tmp_path, '/mnt/usb0/playden/import/Fresh Package', 1, 2)
    with patch.object(NetworkRequest, 'run', autospec=True, return_value=result) as run:
        assert main() == 0
    request = run.call_args.args[0]
    assert request.destination == 'usb0' and request.package_name == 'Fresh Package'
    assert token not in capsys.readouterr().out
